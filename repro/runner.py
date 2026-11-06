"""Frozen trial runner: end-to-end arms and the shared-initial repair experiment.

Three modes
-----------
``initial``  one call per question producing the frozen common initial (COMMON).
``e2e``      independent initials for every arm, then the arm's own pipeline.
``repair``   all repair arms read the *same* frozen common initial.

Discipline carried over from the internal harness
-------------------------------------------------
* the model process never imports the scorer; ``scoring`` in ``sys.modules`` is a
  hard error inside ``invoke``;
* before every request the runner re-hashes the frozen code, manifests and the
  current image, so a drifted dependency cannot silently produce new numbers;
* results are written append-only per shard and merged in manifest order;
* every shard shares a file-locked request cap, and a restart resumes from the
  ledger instead of re-sampling finished logical requests.
"""
import argparse
import collections
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from . import client as API
from . import common as C
from . import config
from . import ssc_baseline as SSC
from evid36 import evid_integrated as E36
from evid36 import fact_first as F36
from evid36.variant import VARIANT as PROMPT_VARIANT

ROOT = C.ROOT
MODEL = {"name": None, "temperature": 0, "thinking": False, "max_tokens": 1200, "retry": 0}
LIMITS = {
    "e2e": {"DIRECT": 1, "REREAD2": 2, "SSC_INIT": 1, "SSC": 2,
            "EVID_FACTS": 1, "EVID_INIT": 1, "EVID": 4},
    "initial": {"COMMON": 1},
    "repair": {"FIX_SSC": 4, "FIX_EVID": 8, "RR_MATCH": 8},
    "fin250": {"REREAD2": 2, "EVID_FACTS": 1, "EVID_INIT": 1, "EVID": 4},
    "pure": {"RR_TOKEN": 16},
}
COMMON = ('Answer the image question once. Return JSON {"answer":"final answer with units/names",'
          '"reasoning":"brief reasoning, at most two sentences"}. Do not review, self-check, correct a '
          'prior answer or output evidence records. If impossible answer:null and explain briefly. '
          'No Markdown.')
DIRECT = ('Answer the question using only the supplied table image. Output only the final answer, '
          'including the unit when applicable. Do not output reasoning, evidence records, or a self-review.')
REREAD = ('Read the ORIGINAL image and question again. The previous answer may be wrong. Independently '
          'redo the requested reading/computation and output only your final answer with units/names. '
          'No correctness verdict, checklist or evidence record.')
REREAD_JSON = REREAD + ' Return JSON {"answer":"...","reasoning":"brief basis for this answer"}, no Markdown.'


class BudgetStop(RuntimeError):
    pass


def _apply_model_config():
    from . import config
    MODEL.update(name=config.MODEL, temperature=config.TEMPERATURE,
                 thinking=config.ENABLE_THINKING, max_tokens=config.MAX_TOKENS, retry=config.RETRY)


def rows(path):
    return C.rows(path)


def dump(path, obj, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=1)
    else:
        temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
        temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, path)


def save_rows(path, records):
    path = Path(path)
    temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    temporary.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                         encoding="utf-8")
    os.replace(temporary, path)


def canonical(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def locked(handle):
    if os.name == "nt":
        import msvcrt
        for _ in range(3000):
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.002)
        raise BudgetStop("quota lock timeout")
    import fcntl
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def unlock(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Quota:
    """Cross-process request cap; one file lock, one integer, no refunds."""

    def __init__(self, folder, cap):
        self.folder = Path(folder)
        self.cap = cap

    def _open(self):
        last = None
        for _ in range(600):
            try:
                return (self.folder / "quota.lock").open("a+b")
            except PermissionError as exc:
                last = exc
                time.sleep(0.01)
        raise BudgetStop("quota lock unavailable: %s" % last)

    def take(self):
        handle = self._open()
        try:
            locked(handle)
            try:
                path = self.folder / "quota.json"
                used = json.loads(path.read_text())["used"] if path.exists() else 0
                if used >= self.cap:
                    raise BudgetStop("batch request cap reached")
                dump(path, {"used": used + 1, "cap": self.cap})
            finally:
                unlock(handle)
        finally:
            handle.close()


def dataset_path(dataset):
    return C.dataset_path(dataset)


def folder(dataset, mode):
    return C.folder(dataset, mode)


def resolve(relative):
    """Resolve a freeze key to a path: code lives in the repository, data in EVID_ROOT."""
    if relative.startswith("code/"):
        return C.REPO_ROOT / relative[len("code/"):]
    return ROOT / relative


def source_files():
    root = Path(__file__).resolve().parent
    files = sorted(list(root.glob("*.py")) + list(root.glob("*.json")))
    files += sorted((ROOT / "evid36").glob("*.py"))
    files += [ROOT / "scripts" / "pipeline.py"]
    return files


def snapshot(dataset):
    runtime = rows(dataset_path(dataset) / "runtime.jsonl")
    n = len(runtime)
    if n == 0 or len({r["sample_id"] for r in runtime}) != n:
        raise ValueError("empty cohort or duplicate sample_id")
    files = {}
    for path in source_files():
        if not path.exists():
            continue
        try:
            key = path.relative_to(ROOT).as_posix()
        except ValueError:                     # EVID_ROOT points outside the repo
            key = "code/" + path.relative_to(C.REPO_ROOT).as_posix()
        files[key] = C.sha256_file(path)
    for path in sorted(dataset_path(dataset).glob("*.json")) + sorted(dataset_path(dataset).glob("*.jsonl")):
        files[path.relative_to(ROOT).as_posix()] = C.sha256_file(path)
    for record in runtime:
        path = ROOT / record["image"]
        digest = C.sha256_file(path)
        if digest != record["image_sha256"]:
            raise ValueError("image drift " + record["sample_id"])
        files[record["image"]] = digest
    model = {"name": config.MODEL, "temperature": config.TEMPERATURE,
             "thinking": config.ENABLE_THINKING, "max_tokens": config.MAX_TOKENS,
             "retry": config.RETRY}
    obj = {"dataset": dataset, "n": n, "model": model, "limits": LIMITS, "files": files,
           "e2e_arms": sorted(config.E2E_ARMS) or "all",
           "prompt_variant": PROMPT_VARIANT,
           "python": sys.version,
           "protocol": ("e2e independent initials; shared frozen answer+reason; max 2 repairs; "
                        "model judgment separate from structural outcome")}
    obj["sha256"] = canonical(obj)
    return obj


def freeze(dataset):
    dump(C.RUNS / dataset / "freeze.json", snapshot(dataset), exclusive=True)


def verify(dataset):
    path = C.RUNS / dataset / "freeze.json"
    if not path.exists():
        raise ValueError("no freeze.json for %s: run `freeze` first" % dataset)
    frozen = json.loads(path.read_text(encoding="utf-8"))
    if frozen != snapshot(dataset):
        raise ValueError("freeze drift: refusing requests")
    return frozen


def initial_path(dataset):
    return folder(dataset, "initial") / "results.jsonl"


def initial_freeze(dataset):
    frozen = verify(dataset)
    path = initial_path(dataset)
    records = rows(path)
    expected = {x["sample_id"] for x in rows(dataset_path(dataset) / "runtime.jsonl")}
    if len(records) != len(expected) or {x["sample_id"] for x in records} != expected \
            or any(x["freeze_sha256"] != frozen["sha256"] for x in records):
        raise ValueError("initial incomplete or wrong cohort")
    dump(folder(dataset, "initial") / "initial_freeze.json",
         {"master_freeze": frozen["sha256"], "results_sha256": C.sha256_file(path),
          "n": len(expected)}, exclusive=True)


def verify_initial(dataset):
    path = folder(dataset, "initial") / "initial_freeze.json"
    frozen = json.loads(path.read_text())
    if frozen["results_sha256"] != C.sha256_file(initial_path(dataset)):
        raise ValueError("common initial drift")
    master = json.loads((C.RUNS / dataset / "freeze.json").read_text())["sha256"]
    if frozen["master_freeze"] != master:
        raise ValueError("initial parent mismatch")
    return frozen


class Invoke:
    """Per-item invocation with role limits, quota, drift checks and receipts."""

    def __init__(self, dataset, mode, tag, frozen, limits=None):
        self.dataset, self.mode, self.freeze = dataset, mode, frozen
        self.dir = folder(dataset, mode)
        self.dir.mkdir(parents=True, exist_ok=True)
        limits = limits or LIMITS[mode]
        self.limits = limits
        n = len(rows(dataset_path(dataset) / "runtime.jsonl"))
        cap = n * sum(limits.values())
        self.quota = Quota(self.dir, cap)
        _apply_model_config()
        API.config.MODEL = MODEL["name"]
        self.caller = API.Caller(
            str(self.dir),
            {"per_sample": limits,
             "per_arm_total": {arm: n * value for arm, value in limits.items()},
             "batch_total": cap},
            run_prefix=mode + "_" + frozen["sha256"][:10] + tag, response_mode="text")

    def for_item(self, item):
        count = collections.Counter()
        image = ROOT / item["image"]
        sid = item["sample_id"]

        def invoke(arm, phase, system, prompt, _image=None):
            if "scoring" in sys.modules:
                raise RuntimeError("the scorer must not run inside the model process")
            for relative, expected in self.freeze["files"].items():
                if (relative.endswith((".py", ".json", ".jsonl")) or relative == item["image"]) \
                        and C.sha256_file(resolve(relative)) != expected:
                    raise ValueError("runtime dependency/image drift " + relative)
            if self.mode == "repair":
                verify_initial(self.dataset)
            count[arm] += 1
            if count[arm] > self.limits.get(arm, 0):
                raise BudgetStop("per-item role limit " + arm)
            full = (system or "") + "\n\n" + prompt
            scope = "%s:%s:%s:%d" % (sid, arm, phase, count[arm])
            raw, meta = None, {}
            for attempt in range(max(1, config.TRANSPORT_ATTEMPTS)):
                # Only transport failures are retried; a truncated or malformed
                # answer is a result, not an outage.
                attempt_scope = scope if attempt == 0 else scope + ":transport%d" % attempt
                fingerprint = self.caller._fingerprint(item["image_sha256"], full, attempt_scope)
                if fingerprint not in self.caller.cache:
                    self.quota.take()
                raw, meta = self.caller.call(arm, sid, str(image), full, purpose=phase,
                                             request_scope=attempt_scope)
                if meta.get("abort"):
                    raise BudgetStop(meta.get("abort_reason", "role quota"))
                if meta.get("status") == "ok":
                    break
                error = str(meta.get("error") or "").lower()
                if any(code in error for code in ("401", "403", "404")):
                    raise RuntimeError("endpoint/auth failure; stop without further requests")
                transient = any(marker in error for marker in
                                ("429", "quota", "rate limit", "timed out", "timeout",
                                 "connection", "ssl", "tls", "502", "503", "504"))
                if not transient or attempt + 1 >= config.TRANSPORT_ATTEMPTS:
                    if transient:
                        raise BudgetStop("infrastructure interruption; cache preserved")
                    break
                time.sleep(2 * (attempt + 1))
            return {"status": meta.get("status"), "content": raw if isinstance(raw, str) else "",
                    **{k: meta.get(k) for k in ("call_id", "finish_reason", "usage",
                                                "cost_cny", "requests", "reused", "error")}}
        return invoke


def parse_common(resp):
    obj = E36.parse(resp)
    if not obj or not E36.valid_answer(obj.get("answer")):
        return None
    return {"answer": obj["answer"],
            "reasoning": obj.get("reasoning") if isinstance(obj.get("reasoning"), str) else ""}


def text_answer(resp):
    return resp.get("content", "").strip() or None \
        if resp.get("status") == "ok" and resp.get("finish_reason") == "stop" else None


def call_json(item, invoke, arm, phase, prompt, data):
    resp = invoke(arm, phase, "", prompt + "\n" + json.dumps(data, ensure_ascii=False), item["image"])
    return parse_common(resp), resp


def ssc_initial(item, invoke):
    prompts, _ = SSC.modules()
    benchmark = "faketab" if item["dataset"] in ("fintabnetqa", "finqa_rendered") else "wikitq"
    system, user = prompts.get_prompts(benchmark, "step1")
    resp = invoke("SSC_INIT", "initial", system,
                  user.format(table="[See the supplied table image.]", question=item["question"]),
                  item["image"])
    return SSC.parse_initial(resp), resp


def run_e2e(item, invoke, with_ssc=True, with_evid=True):
    """Run the end-to-end arms.

    ``config.E2E_ARMS`` (env ``EVID_E2E_ARMS``) restricts the executed arms.  It
    exists for the cross-model replication, where only the two arms under test
    (SSC, EVID) are re-run on the second backbone; every other arm is absent
    from the record rather than filled with a zero.
    """
    requested = config.E2E_ARMS
    record = {"sample_id": item["sample_id"], "dataset": item["dataset"],
              "initials": {}, "arms": {}, "initial_calls": {}}
    if not requested or "direct" in requested:
        direct = invoke("DIRECT", "answer_once", DIRECT, "Question: " + item["question"], item["image"])
        record["initials"]["DIRECT"] = {"answer": text_answer(direct)}
        record["arms"]["DIRECT"] = {"answer": text_answer(direct),
                                    "status": "ANSWERED" if text_answer(direct) is not None else "UNSUBMITTED",
                                    "calls": [direct]}
    if not requested or "reread2" in requested:
        first = invoke("REREAD2", "read1", DIRECT, "Question: " + item["question"], item["image"])
        a = text_answer(first)
        second = invoke("REREAD2", "read2", REREAD,
                        json.dumps({"question": item["question"], "previous_answer": a},
                                   ensure_ascii=False),
                        item["image"])
        b = text_answer(second)
        record["initials"]["REREAD2"] = {"answer": a}
        record["arms"]["REREAD2"] = {"answer": b if b is not None else a,
                                     "status": "REREAD" if b is not None else "FALLBACK_READ1",
                                     "calls": [first, second]}
    if with_ssc and (not requested or "ssc" in requested):
        initial, ssc_resp = ssc_initial(item, invoke)
        ssc = SSC.finish(item, initial, invoke) if initial else {"answer": None, "status": "INITIAL_MISSING", "calls": []}
        record["initials"]["SSC"] = initial
        record["arms"]["SSC"] = ssc
        record["initial_calls"]["SSC"] = ssc_resp
    if with_evid and (not requested or "evid" in requested):
        evid_initial, evid_record = F36.initial(item, invoke)
        evid = E36.run(item, evid_initial, invoke) if evid_initial else \
            {"answer": None, "status": "INITIAL_MISSING", "calls": []}
        record["initials"]["EVID"] = evid_initial
        record["arms"]["EVID"] = evid
        record["initial_calls"]["EVID"] = evid_record
    return record


# ------------------------------------------------------------------ repair
def remap_evidence(initial, prefix):
    out = copy.deepcopy(initial)
    pool = out.get("evidence", [])
    mapping = {r["id"]: prefix + r["id"] for r in pool}
    for record in pool:
        record["id"] = mapping[record["id"]]
        for key in ("operand_ids", "supersedes", "contradicts", "superseded_by", "disputed_by"):
            if key in record:
                record[key] = [mapping.get(x, x) for x in record[key]]
    if "supporting_ids" in out:
        out["supporting_ids"] = [mapping.get(x, x) for x in out["supporting_ids"]]
    return out


def carry(previous, out, prefix):
    carried = copy.deepcopy(previous)
    carried["answer"] = out["answer"]
    if out.get("status") == "CORRECTED_EVIDENCE_SUPPORTED":
        repair = out.get("repair") or {}
        carried["reasoning"] = repair.get("reasoning") or repair.get("analysis") or carried.get("reasoning", "")
    versions = out.get("evidence_versions", [])
    carried["evidence"] = versions[-1]["evidence"] if versions else out.get("initial_evidence", carried.get("evidence", []))
    carried["scope"] = ((out.get("final_check") or {}).get("scope")
                        or (out.get("repair") or {}).get("scope") or carried.get("scope", "OPEN"))
    carried["unresolved"] = list(out.get("unresolved", []))
    return remap_evidence(carried, prefix)


def verdict(out, ssc=False):
    if ssc:
        return (out.get("events") or [{}])[0].get("verdict", "parse_error")
    return (out.get("check") or {}).get("verdict", "parse_error")


def repairs(item, common, invoke, with_ssc=True):
    frozen_common = canonical(common)
    arms = {}
    budgets = []
    if common is None:
        return {"sample_id": item["sample_id"], "dataset": item["dataset"], "common": None,
                "arms": {a: {"answer": None, "rounds": [], "status": "INITIAL_MISSING"}
                         for a in (("KEEP", "SSC", "EVID", "RR_MATCH") if with_ssc else ("KEEP", "EVID", "RR_MATCH"))},
                "shared_unchanged": True}
    plan = [("EVID", E36, "FIX_EVID")]
    if with_ssc:
        plan.insert(0, ("SSC", None, "FIX_SSC"))
    for name, method, role in plan:
        current = copy.deepcopy(common)
        record = []
        stopped = False
        for round_index in (1, 2):
            if stopped:
                record.append({"round": round_index, "executed": False, "answer": current["answer"],
                               "judgment": "not_executed", "reasoning": current.get("reasoning", ""),
                               "status": "EARLY_STOP_CARRIED_FORWARD", "calls": 0})
                continue
            used = []

            def wrapped(_arm, phase, system, prompt, image=None, _used=used, _name=name):
                if len(_used) >= (2 if _name == "SSC" else 4):
                    raise BudgetStop("round request limit")
                result = invoke(role, "r%d.%s" % (round_index, phase), system, prompt, image)
                _used.append(result)
                return result

            before = current["answer"]
            out = SSC.finish(item, current, wrapped) if name == "SSC" else method.run(item, current, wrapped)
            answer = out.get("answer")
            judgment = verdict(out, name == "SSC")
            record.append({"round": round_index, "executed": True, "answer": answer,
                           "judgment": judgment, "status": out["status"], "calls": len(used), "trace": out})
            if name == "SSC":
                analysis = (out.get("events") or [{}])[0].get("error_analysis", "")
                rationale = current.get("reasoning", "") if answer == before else \
                    ("No revised reasoning emitted by the author protocol. Prior error analysis "
                     "(not a new rationale): " + analysis)
                current = {"answer": answer, "reasoning": rationale}
                stopped = judgment == "correct" and answer == before
            else:
                current = carry(current, out, "prev%d_" % round_index)
                stopped = out["status"] in ("KEPT_EVIDENCE_SUPPORTED", "KEPT_AFTER_ACQUISITION") \
                    and answer == before
            if not E36.valid_answer(answer):
                stopped = True
        arms[name] = {"answer": current["answer"], "rounds": record}
        if name == "EVID":
            budgets = [r["calls"] for r in record]
    current = copy.deepcopy(common)
    rec = []
    for round_index, calls in enumerate(budgets, 1):
        for index in range(calls):
            candidate, _ = call_json(item, invoke, "RR_MATCH", "r%d.reread%d" % (round_index, index + 1),
                                     REREAD_JSON,
                                     {"question": item["question"], "previous_answer": current["answer"],
                                      "previous_reasoning": current.get("reasoning", "")})
            if candidate is not None:
                current = candidate
        rec.append({"round": round_index, "executed": calls > 0, "answer": current["answer"],
                    "reasoning": current.get("reasoning", ""), "judgment": "not_applicable",
                    "status": "BUDGET_MATCHED_REREAD", "calls": calls})
    arms["RR_MATCH"] = {"answer": current["answer"], "rounds": rec,
                        "matched_to": "EVID logical unique calls per round; no answer/label transfer"}
    arms["KEEP"] = {"answer": common["answer"],
                    "rounds": [{"round": r, "answer": common["answer"], "executed": False,
                                "judgment": "not_applicable", "calls": 0, "status": "KEEP_INITIAL"}
                               for r in (1, 2)]}
    if canonical(common) != frozen_common:
        raise RuntimeError("shared initial mutated")
    return {"sample_id": item["sample_id"], "dataset": item["dataset"], "common": common,
            "common_sha256": frozen_common, "arms": arms, "shared_unchanged": True}


# ------------------------------------------------------------------ follow-ups
def fin250_trial(item, invoke):
    """Public-benchmark arm pair on FinTabNetQA 250: REREAD2 vs EVID."""
    first = invoke("REREAD2", "read1", DIRECT, "Question: " + item["question"], item["image"])
    a = text_answer(first)
    second = invoke("REREAD2", "read2", REREAD,
                    json.dumps({"question": item["question"], "previous_answer": a}, ensure_ascii=False),
                    item["image"])
    b = text_answer(second)
    evid_initial, evid_record = F36.initial(item, invoke)
    evid = E36.run(item, evid_initial, invoke) if evid_initial else \
        {"answer": None, "status": "INITIAL_MISSING", "calls": []}
    return {"sample_id": item["sample_id"], "dataset": item["dataset"],
            "initials": {"REREAD2": {"answer": a}, "EVID": evid_initial},
            "arms": {"REREAD2": {"answer": b if b is not None else a,
                                 "status": "REREAD" if b is not None else "FALLBACK_READ1",
                                 "calls": [first, second]},
                     "EVID": evid},
            "initial_calls": {"EVID": evid_record}}


def vote(values):
    """Fixed-rule majority over normalised strings; ties go to the earliest answer."""
    import unicodedata
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    keys = [" ".join(unicodedata.normalize("NFKC", v).casefold().split()) for v in valid]
    counts = collections.Counter(keys)
    winner = max(counts, key=lambda key: counts[key])
    return valid[keys.index(winner)]


def pure_trajectory(item, budget, invoke):
    """Independent re-reads of the same image/question until the token budget is met.

    No previous answer, evidence, diagnosis or verdict is shown, so this control
    can only differ from EVID by how it spends its budget.
    """
    tokens = 0
    answers = []
    points = [{"tokens": 0, "answer": None, "step": 0}]
    responses = []
    unknown = False
    for index in range(config.MAX_READS):
        response = invoke("RR_TOKEN", "independent_read%d" % (index + 1), "",
                          DIRECT + "\nQuestion: " + item["question"], item["image"])
        responses.append(response)
        answers.append(text_answer(response))
        usage = response.get("usage") or {}
        if "prompt_tokens" not in usage or "completion_tokens" not in usage:
            unknown = True
        else:
            tokens += usage["prompt_tokens"] + usage["completion_tokens"]
        points.append({"tokens": tokens, "answer": vote(answers), "step": index + 1})
        if tokens >= budget or unknown:
            break
    return {"target_tokens": budget, "actual_tokens": tokens, "reached": tokens >= budget,
            "usage_unknown": unknown, "answer": vote(answers),
            "before": [p for p in points if p["tokens"] <= budget][-1],
            "after": next((p for p in points if p["tokens"] >= budget), None),
            "answers": answers, "checkpoints": points, "responses": responses,
            "status": "BUDGET_REACHED" if tokens >= budget else "ATTEMPT_CAP"}


# ------------------------------------------------------------------ driver
def run(dataset, mode, shard, with_ssc=True):
    frozen = verify(dataset)
    runtime = rows(dataset_path(dataset) / "runtime.jsonl")
    out_dir = folder(dataset, mode)
    out_dir.mkdir(parents=True, exist_ok=True)
    if (out_dir / "quarantined.json").exists():
        raise ValueError("quarantined run")
    index, shards = map(int, shard.split("/"))
    tag = "_s%d" % index
    path = out_dir / ("results" + tag + ".jsonl")
    if not 0 <= index < shards:
        raise ValueError("bad shard")
    originals = {}
    if mode == "repair":
        verify_initial(dataset)
        originals = {r["sample_id"]: r["initial"] for r in rows(initial_path(dataset))}
    with (out_dir / ("run" + tag + ".lock")).open("a+b") as lock:
        locked(lock)
        try:
            output = rows(path) if path.exists() else []
            done = {r["sample_id"] for r in output}
            if len(output) != len(done) or any(r["freeze_sha256"] != frozen["sha256"] for r in output):
                raise ValueError("mixed or duplicate result")
            transport = Invoke(dataset, mode, tag, frozen)
            for position, item in enumerate(runtime):
                if position % shards != index or item["sample_id"] in done:
                    continue
                try:
                    invoke = transport.for_item(item)
                    if mode == "initial":
                        initial, resp = call_json(item, invoke, "COMMON", "generate_shared", COMMON,
                                                  {"question": item["question"]})
                        record = {"sample_id": item["sample_id"], "dataset": item["dataset"],
                                  "initial": initial, "response": resp}
                    elif mode == "e2e":
                        record = run_e2e(item, invoke, with_ssc=with_ssc)
                    elif mode == "fin250":
                        record = fin250_trial(item, invoke)
                    elif mode == "pure":
                        budgets = json.loads((dataset_path(dataset) / "pure_budget.json").read_text())
                        record = pure_trajectory(item, budgets[item["sample_id"]], invoke)
                    else:
                        record = repairs(item, copy.deepcopy(originals[item["sample_id"]]), invoke,
                                         with_ssc=with_ssc)
                except BudgetStop as exc:
                    print(json.dumps({"stopped": str(exc), "sample_id": item["sample_id"]}), flush=True)
                    raise
                except Exception as exc:
                    dump(out_dir / "quarantined.json",
                         {"sample_id": item["sample_id"], "error": type(exc).__name__ + ":" + str(exc)},
                         exclusive=True)
                    raise
                record["freeze_sha256"] = frozen["sha256"]
                output.append(record)
                save_rows(path, output)
            print(json.dumps({"mode": mode, "dataset": dataset, "shard": shard,
                              "completed": len(output)}), flush=True)
        finally:
            unlock(lock)


def merge(dataset, mode, shards):
    frozen = verify(dataset)
    out_dir = folder(dataset, mode)
    runtime = rows(dataset_path(dataset) / "runtime.jsonl")
    n = len(runtime)
    order = {r["sample_id"]: i for i, r in enumerate(runtime)}
    merged = [r for k in range(shards) for r in rows(out_dir / ("results_s%d.jsonl" % k))]
    if len(merged) != n or len({r["sample_id"] for r in merged}) != n \
            or any(r["sample_id"] not in order or r["freeze_sha256"] != frozen["sha256"] for r in merged):
        raise ValueError("incomplete/duplicate/cross-version merge")
    merged.sort(key=lambda r: order[r["sample_id"]])
    save_rows(out_dir / "results.jsonl", merged)
    print(json.dumps({"merged": len(merged), "mode": mode, "dataset": dataset}))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Frozen trials for SSC-EVID.")
    parser.add_argument("command", choices=["budget", "freeze", "verify", "run", "merge", "freeze-initial"])
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--mode", choices=list(LIMITS), default="e2e")
    parser.add_argument("--shard", default="0/1")
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--authorize-paid", action="store_true")
    parser.add_argument("--no-ssc", action="store_true",
                        help="skip the SSC arm (its author code is not redistributed)")
    args = parser.parse_args(argv)
    _apply_model_config()
    n = len(rows(dataset_path(args.dataset) / "runtime.jsonl"))
    if args.command == "budget":
        print(json.dumps({"dataset": args.dataset, "n": n,
                          "caps": {m: n * sum(v.values()) for m, v in LIMITS.items()}}, ensure_ascii=False))
    elif args.command == "freeze":
        freeze(args.dataset)
        print(json.dumps({"sha256": verify(args.dataset)["sha256"]}))
    elif args.command == "verify":
        print(json.dumps({"sha256": verify(args.dataset)["sha256"], "drift": []}))
    elif args.command == "freeze-initial":
        initial_freeze(args.dataset)
    elif args.command == "merge":
        merge(args.dataset, args.mode, args.shards)
    elif args.command == "run":
        if not args.authorize_paid:
            parser.error("explicit --authorize-paid required")
        run(args.dataset, args.mode, args.shard, with_ssc=not args.no_ssc)


if __name__ == "__main__":
    main()
