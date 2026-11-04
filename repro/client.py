"""The only place that sends a request or writes a ledger row.

Everything the model sees goes through one function, so the audit trail is
complete: request body, image hash, fingerprint, usage, finish reason, latency,
cost estimate and the raw response text. A completed logical request can be
replayed from the cache after an interruption instead of being sampled again.

Fingerprint = sha256(model | full prompt | image sha | max_tokens | temperature |
thinking | logical request scope). The arm name is deliberately NOT part of it, so
identical prefixes are shared across arms and a deleted module costs nothing extra.
"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import common as C
from . import config
from .json_helpers import parse_json_block


class Credentials:
    def __init__(self):
        values = config.load_credentials()
        self.base_url = values["base_url"]
        self.api_key = values["api_key"]
        if config.ALLOWED_HOST:
            host = urlparse(self.base_url).hostname
            if host != config.ALLOWED_HOST:
                raise ValueError(
                    "Endpoint host %r is not the pinned EVID_ALLOWED_HOST %r; refusing to send."
                    % (host, config.ALLOWED_HOST))


def load_env(cred_path=None):
    """Backwards-compatible helper: returns the base URL / key pair as a dict."""
    if cred_path:
        os.environ.setdefault("EVID_CRED_FILE", str(cred_path))
    values = config.load_credentials()
    return {"BAILIAN_BASE_URL": values["base_url"], "BAILIAN_API_KEY": values["api_key"]}


class Caller:
    """VLM transport with fingerprint reuse, per-item quotas and a full ledger."""

    def __init__(self, rec_dir=None, quota=None, run_prefix="run",
                 response_mode="json", cred_path=None, batch_state=None):
        if response_mode not in ("json", "text"):
            raise ValueError("response_mode must be json or text")
        self.response_mode = response_mode
        self.credentials = Credentials() if cred_path is None else _CredentialsFrom(cred_path)
        self.rec_dir = str(rec_dir or C.RUNS)
        self.calls_path = os.path.join(self.rec_dir, "%s_calls.jsonl" % run_prefix.lower())
        # json mode stores parsed objects, text mode stores strings: separate files.
        self.cache_path = os.path.join(
            self.rec_dir,
            "%s_calls_cache%s.json" % (run_prefix.lower(), "" if response_mode == "json" else "_text"))
        quota = quota or {}
        if not isinstance(quota, dict) or "per_sample" in quota or "batch_total" in quota:
            self.per_sample = dict(quota.get("per_sample") or {})
            self.per_arm_total = dict(quota.get("per_arm_total") or {})
            self.batch_total = quota.get("batch_total", 64)
            probe = quota.get("probe_used", 0)
        else:                                        # legacy scalar form
            self.per_sample = {"*": quota.get("per_question", 4)}
            self.per_arm_total = {}
            self.batch_total = quota.get("per_batch", 63)
            probe = quota.get("probe_used", 1)
        self.cache = {}
        if os.path.exists(self.cache_path):
            with open(self.cache_path, encoding="utf-8") as handle:
                self.cache = json.load(handle)
        # Resume: restore per-item and per-arm counters from the ledger.
        init_n = probe
        self.q_count = {}
        self.arm_total = {}
        if os.path.exists(self.calls_path):
            with open(self.calls_path, encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    if record.get("reused"):
                        continue
                    init_n += 1
                    key = (record.get("arm"), record.get("sid"))
                    self.q_count[key] = self.q_count.get(key, 0) + 1
                    arm = record.get("arm") or "*"
                    self.arm_total[arm] = self.arm_total.get(arm, 0) + 1
        self.batch = batch_state if batch_state is not None else {"real_requests": init_n}
        if batch_state is not None and "real_requests" not in batch_state:
            batch_state["real_requests"] = init_n
        self.requests_before_this_run = init_n
        self.records = []
        self.run_id = "%s-%s" % (run_prefix, time.strftime("%Y%m%d-%H%M%S"))

    # ------------------------------------------------------------ quota
    def _limit_hit(self, arm, sid):
        limit_item = self.per_sample.get(arm, self.per_sample.get("*"))
        if limit_item is not None and self.q_count.get((arm, sid), 0) >= limit_item:
            return "per-item request cap %s reached (%s/%s)" % (limit_item, arm, sid)
        limit_arm = self.per_arm_total.get(arm, self.per_arm_total.get("*"))
        if limit_arm is not None and self.arm_total.get(arm, 0) >= limit_arm:
            return "per-arm request cap %s reached (%s)" % (limit_arm, arm)
        if self.batch["real_requests"] >= self.batch_total:
            return "batch request cap %s reached" % self.batch_total
        return None

    def _fingerprint(self, image_sha, prompt, request_scope=None):
        digest = hashlib.sha256()
        for part in (config.MODEL, prompt, image_sha, str(config.MAX_TOKENS),
                     str(config.TEMPERATURE), str(config.ENABLE_THINKING)):
            digest.update(part.encode())
        if request_scope is not None:
            digest.update(("\0logical-request:" + request_scope).encode())
        if self.response_mode != "json":
            digest.update(b"\0response-mode:text-v1")
        return digest.hexdigest()

    def _parse_response(self, text):
        if self.response_mode == "text":
            return text.strip(), None
        return parse_json_block(text)

    # ------------------------------------------------------------ call
    def call(self, arm, sid, image_path, prompt, purpose, request_scope=None):
        """Send one request; returns ``(parsed_or_text, meta)``."""
        image_sha = C.sha256_file(image_path) if image_path else "text-only"
        fingerprint = self._fingerprint(image_sha, prompt, request_scope)
        if fingerprint in self.cache:
            cached = self.cache[fingerprint]
            self.records.append({**cached.get("_ledger", {}),
                                 "run_id": self.run_id, "arm": arm, "sid": sid,
                                 "request_scope": request_scope,
                                 "response_mode": self.response_mode,
                                 "call_id": "reused-%s" % fingerprint[:12],
                                 "purpose": purpose, "fingerprint": fingerprint,
                                 "reused": True, "model_requested": config.MODEL,
                                 "model_returned": cached.get("model_returned"),
                                 "status": "ok", "usage": cached.get("usage"),
                                 "latency_s": cached.get("latency_s"),
                                 "cost_cny": 0.0,
                                 "original_cost_cny": cached.get("original_cost_cny"),
                                 "finish_reason": cached.get("finish_reason"),
                                 "cost_note": "fingerprint reuse: not billed twice",
                                 "prompt_text": prompt,
                                 "image_sha256": image_sha,
                                 "response_text": cached.get("response_text"),
                                 "first_call_id": cached.get("first_call_id")})
            self._flush()
            return cached["parsed"], {"requests": 0, "reused": True, "status": "ok",
                                      "call_id": cached.get("first_call_id"),
                                      "first_call_id": cached.get("first_call_id"),
                                      "fingerprint": fingerprint,
                                      "finish_reason": cached.get("finish_reason"),
                                      "response_text": cached.get("response_text"),
                                      "note": "fingerprint reuse"}
        hit = self._limit_hit(arm, sid)
        if hit:
            return None, {"requests": 0, "abort": True, "abort_reason": hit}
        if image_path:
            with open(image_path, "rb") as handle:
                encoded = base64.b64encode(handle.read()).decode()
            content = [{"type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + encoded}},
                       {"type": "text", "text": prompt}]
        else:
            content = [{"type": "text", "text": prompt}]
        body = {"model": config.MODEL,
                "messages": [{"role": "user", "content": content}],
                "temperature": config.TEMPERATURE,
                "enable_thinking": config.ENABLE_THINKING,
                "max_tokens": config.MAX_TOKENS}
        started = time.time()
        call_id = "%s-%s-%s-%d" % (self.run_id, arm, sid, self.batch["real_requests"] + 1)
        status, error, data = "ok", None, {}
        try:
            request = urllib.request.Request(
                self.credentials.base_url + "/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + self.credentials.api_key})
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            status, error = "error", "HTTPError: %s %s" % (exc.code, str(exc.reason)[:160])
        except Exception as exc:
            status, error = "error", "%s: %s" % (type(exc).__name__, str(exc)[:200])
        latency = round(time.time() - started, 2)
        usage = (data.get("usage") or {}) if status == "ok" else {}
        cost = None
        if status == "ok" and "prompt_tokens" in usage and "completion_tokens" in usage:
            cost = (usage.get("prompt_tokens", 0) * config.PRICE_INPUT
                    + usage.get("completion_tokens", 0) * config.PRICE_OUTPUT) / 1e6
        try:
            text = data["choices"][0]["message"].get("content") or ""
            finish = data["choices"][0].get("finish_reason")
        except Exception:
            text, finish = "", None
        parsed, parse_error = self._parse_response(text)
        record = {"run_id": self.run_id, "arm": arm, "sid": sid,
                  "request_scope": request_scope,
                  "response_mode": self.response_mode,
                  "call_id": call_id, "purpose": purpose, "fingerprint": fingerprint,
                  "reused": False, "model_requested": config.MODEL,
                  "model_returned": data.get("model"), "status": status,
                  "error": error, "finish_reason": finish,
                  "latency_s": latency, "usage": usage,
                  "cost_cny": (round(cost, 6) if cost is not None else None),
                  "prompt_text": prompt,
                  "image_path": (os.path.relpath(image_path, C.ROOT) if image_path else None),
                  "image_sha256": image_sha, "response_text": text,
                  "json_parse_error": parse_error}
        self.records.append(record)
        self.batch["real_requests"] += 1
        self.q_count[(arm, sid)] = self.q_count.get((arm, sid), 0) + 1
        self.arm_total[arm] = self.arm_total.get(arm, 0) + 1
        if status == "ok" and (parsed or self.response_mode == "text"):
            self.cache[fingerprint] = {"usage": usage, "response_text": text,
                                       "parsed": parsed,
                                       "finish_reason": finish,
                                       "model_returned": data.get("model"),
                                       "latency_s": latency,
                                       "original_cost_cny": (round(cost, 6) if cost is not None else None),
                                       "first_call_id": call_id,
                                       "first_run_id": self.run_id}
        self._flush()
        return (parsed if status == "ok" else None), {
            "requests": 1, "reused": False,
            "note": "real call %s" % call_id,
            "call_id": call_id, "status": status,
            "cost_cny": (round(cost, 6) if cost is not None else None),
            "usage": usage, "latency_s": latency, "error": error,
            "response_text": text, "finish_reason": finish}

    def _flush(self):
        os.makedirs(self.rec_dir, exist_ok=True)
        with open(self.calls_path, "a", encoding="utf-8") as handle:
            for record in self.records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.records = []
        temporary = self.cache_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.cache, handle, ensure_ascii=False)
        os.replace(temporary, self.cache_path)


class _CredentialsFrom(Credentials):
    def __init__(self, cred_path):
        saved = os.environ.get("EVID_CRED_FILE")
        os.environ["EVID_CRED_FILE"] = str(cred_path)
        try:
            super().__init__()
        finally:
            if saved is None:
                os.environ.pop("EVID_CRED_FILE", None)
            else:
                os.environ["EVID_CRED_FILE"] = saved
