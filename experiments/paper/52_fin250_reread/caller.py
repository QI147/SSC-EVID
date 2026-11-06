# -*- coding: utf-8 -*-
"""统一实验入口｜VLM 调用层与账本（零 API 调用；真实调用仅经本层）。

来源: d20_run.py:99-340（Caller / load_env / sha256_file）整体迁移，
本文件为两臂共享的**唯一**调用与记账入口。相对原版的三处改造：

  1. 凭据路径参数化：默认沿用旧 CRED 路径，可用环境变量
     BAILIAN_CRED_FILE 覆盖（原版硬编码在 d20_run.py:43）；
  2. 限额改为**分项配额**（C1 冻结配置的 64 硬顶分解）：
     quota = {"per_sample": {臂: n}, "per_arm_total": {臂: n},
              "batch_total": N, "probe_used": k}
     检查顺序 per_sample(臂,题) → per_arm_total(臂) → batch_total，
     任一触顶即 RESOURCE_ABORT，**不挪用他项配额**；
     兼容旧标量口径 per_question=N → 所有臂统一 N；
  3. 记录文件名按 run_prefix 命名（c1_calls.jsonl / c1_calls_cache.json），
     支持不同实验各自独立记录目录。

账本纪律（共同实施手册）：每次调用记 run_id/call_id/完整响应/usage/
finish_reason/费用/latency；重启续跑从 calls.jsonl 恢复计数；失败不删、
无 usage 标 estimated 不写零；复用响应记 call_id="reused-…" 可溯源首调。
指纹 = sha256(模型+提示+图像sha+max_tokens+温度+思考开关+逻辑request_scope)，
不含实验臂（D22 规则冻结第 6 条）。
"""
import base64
import hashlib
import json
import os
import sys
import time
import urllib.request

import common as C
from json_helpers import parse_json_block          # 来源: d20_run.py:53 同款引用

# D11 冻结模型参数（两臂相同——C1 对照口径）
MODEL = "qwen3.8-flash"
TEMP = 0
THINK = False
MAXTOK = 1200
RETRY = 0
PRICE = {"in": 0.8, "out": 2.7}               # 元/百万 token（D2 冻结）

CRED_DEFAULT = None


def load_env(cred_path=None):
    """读凭据 env。（来源: d20_run.py:99-106；路径可参数化）"""
    if os.environ.get("BAILIAN_BASE_URL") and os.environ.get("BAILIAN_API_KEY"):
        return {k:os.environ[k] for k in ("BAILIAN_BASE_URL","BAILIAN_API_KEY")}
    p = cred_path or os.environ.get("BAILIAN_CRED_FILE") or CRED_DEFAULT
    if not p or not os.path.exists(p):
        raise SystemExit("凭据文件不存在：%s（可用环境变量 BAILIAN_CRED_FILE 覆盖）" % p)
    env = {}
    with open(p, encoding="utf-8") as fh:
        for l in fh:
            l = l.strip()
            if "=" in l and not l.startswith("#"):
                k, v = l.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    return env


class Caller:
    """VLM 调用层：指纹复用 + 分项配额硬检查 + 全量记录链。

    参数：
      rec_dir        记录目录（默认 C1 统一账本目录 REC_C1）
      quota          分项配额 dict（见模块头）；或旧式 per_question 标量
      run_prefix     run_id 前缀（"C1"）
      response_mode  "json"（B2-D 定向提取/时间范围）或 "text"（B2-R 重读）
    """

    def __init__(self, rec_dir=None, quota=None, run_prefix="C1",
                 response_mode="json", cred_path=None, batch_state=None):
        if response_mode not in ("json", "text"):
            raise ValueError("response_mode must be json or text")
        self.response_mode = response_mode
        self.env = load_env(cred_path)
        self.rec_dir = rec_dir or C.REC_C1
        self.calls_path = os.path.join(self.rec_dir,
                                       "%s_calls.jsonl" % run_prefix.lower())
        # 缓存按响应模式分文件（json 模式存 parsed dict，text 模式存字符串；
        # 混写会互相覆盖）。账本仍共用同一个 calls.jsonl。
        self.cache_path = os.path.join(
            self.rec_dir, "%s_calls_cache%s.json"
            % (run_prefix.lower(), "" if response_mode == "json" else "_text"))
        # ---- 配额归一（分项 dict 或旧式标量）
        if quota is None:
            quota = {}
        if not isinstance(quota, dict) or "per_sample" in quota \
                or "batch_total" in quota:
            self.per_sample = dict(quota.get("per_sample") or {})
            self.per_arm_total = dict(quota.get("per_arm_total") or {})
            self.batch_total = quota.get("batch_total", 64)
            probe = quota.get("probe_used", 0)
        else:                                        # 旧式标量（per_question=n）
            self.per_sample = {"*": quota.get("per_question", 4)}
            self.per_arm_total = {}
            self.batch_total = quota.get("per_batch", 63)
            probe = quota.get("probe_used", 1)
        self.cache = {}
        if os.path.exists(self.cache_path):
            with open(self.cache_path, encoding="utf-8") as fh:
                self.cache = json.load(fh)
        # ---- 重启续跑：从 calls.jsonl 恢复计数（批次/每题/每臂），限额不丢
        # （v2 注记迁移自 d20_run.py:194-208：恢复后不得再重置 q_count）
        init_n = probe
        self.q_count = {}
        self.arm_total = {}
        if os.path.exists(self.calls_path):
            with open(self.calls_path, encoding="utf-8") as fh:
                for l in fh:
                    try:
                        r = json.loads(l)
                    except Exception:
                        continue
                    if r.get("reused"):
                        continue
                    init_n += 1
                    k = (r.get("arm"), r.get("sid"))
                    self.q_count[k] = self.q_count.get(k, 0) + 1
                    a = r.get("arm") or "*"
                    self.arm_total[a] = self.arm_total.get(a, 0) + 1
        self.batch = batch_state if batch_state is not None else {
            "real_requests": init_n}
        if batch_state is not None and "real_requests" not in batch_state:
            batch_state["real_requests"] = init_n
        self.requests_before_this_run = init_n
        self.records = []
        self.run_id = "%s-%s" % (run_prefix, time.strftime("%Y%m%d-%H%M%S"))

    # ---- 限额检查（分项配额；触顶即停不挪用）
    def _limit_hit(self, arm, sid):
        lim_s = self.per_sample.get(arm, self.per_sample.get("*"))
        if lim_s is not None and self.q_count.get((arm, sid), 0) >= lim_s:
            return "每臂每题请求上限 %s 已用满（%s/%s）" % (lim_s, arm, sid)
        lim_a = self.per_arm_total.get(arm, self.per_arm_total.get("*"))
        if lim_a is not None and self.arm_total.get(arm, 0) >= lim_a:
            return "该臂全批请求上限 %s 已用满（%s）" % (lim_a, arm)
        if self.batch["real_requests"] >= self.batch_total:
            return "全批请求上限 %s 已用满（分项配额不挪用）" % self.batch_total
        return None

    def _fingerprint(self, image_sha, prompt, request_scope=None):
        """指纹不含臂（D22 冻结第 6 条）；request_scope 区分逻辑轮次。"""
        h = hashlib.sha256()
        for x in (MODEL, prompt, image_sha, str(MAXTOK), str(TEMP), str(THINK)):
            h.update(x.encode())
        if request_scope is not None:
            h.update(("\0logical-request:" + request_scope).encode())
        if self.response_mode != "json":
            h.update(b"\0response-mode:text-v1")
        return h.hexdigest()

    def _parse_response(self, text):
        if self.response_mode == "text":
            return text.strip(), None
        return parse_json_block(text)

    def call(self, arm, sid, image_path, prompt, purpose, request_scope=None):
        """真实 VLM 调用；返回 (parsed_or_None, meta)。发请求前先查分项配额。

        ``image_path=None`` 触发纯文本分支（无图像角色），指纹统一记 "text-only"。
        """
        image_sha = C.sha256_file(image_path) if image_path else "text-only"
        fp = self._fingerprint(image_sha, prompt, request_scope)
        if fp in self.cache:
            c = self.cache[fp]
            self.records.append({**c.get("_ledger", {}),
                                  "run_id": self.run_id, "arm": arm, "sid": sid,
                                  "request_scope": request_scope,
                                  "response_mode": self.response_mode,
                                  "call_id": "reused-%s" % fp[:12],
                                  "purpose": purpose, "fingerprint": fp,
                                  "reused": True, "model_requested": MODEL,
                                  "model_returned": c.get("model_returned"),
                                  "status": "ok", "usage": c.get("usage"),
                                  "latency_s": c.get("latency_s"),
                                  "cost_cny": 0.0,
                                  "original_cost_cny": c.get("original_cost_cny"),
                                  "finish_reason": c.get("finish_reason"),
                                  "cost_note": "指纹复用：不重复计费（原始费用另列）",
                                  "prompt_text": prompt,
                                  "image_sha256": image_sha,
                                  "response_text": c.get("response_text"),
                                  "first_call_id": c.get("first_call_id")})
            self._flush()
            return c["parsed"], {"requests": 0, "reused": True, "status": "ok",
                                 "call_id": c.get("first_call_id"),
                                 "first_call_id": c.get("first_call_id"),
                                 "fingerprint": fp,
                                 "finish_reason": c.get("finish_reason"),
                                 "response_text": c.get("response_text"),
                                 "note": "指纹复用（未发请求，call_id=%s）"
                                         % c.get("first_call_id")}
        hit = self._limit_hit(arm, sid)
        if hit:
            return None, {"requests": 0, "abort": True, "abort_reason": hit}
        # ---- 真实请求
        # image_path=None 表示**纯文本调用**（无图像角色，如 MAS 的 Controller）：
        # 请求体不带 image_url，指纹用固定标记 "text-only"，账本字段不变。
        # 既有调用方全部传真实路径，行为不受影响（向后兼容的可选分支）。
        if image_path:
            with open(image_path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            content = [{"type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + b64}},
                       {"type": "text", "text": prompt}]
        else:
            content = [{"type": "text", "text": prompt}]
        body = {"model": MODEL,
                "messages": [{"role": "user", "content": content}],
                "temperature": TEMP, "enable_thinking": THINK,
                "max_tokens": MAXTOK}
        t0 = time.time()
        call_id = "%s-%s-%s-%d" % (self.run_id, arm, sid,
                                   self.batch["real_requests"] + 1)
        status, err, data = "ok", None, {}
        try:
            req = urllib.request.Request(
                self.env["BAILIAN_BASE_URL"].rstrip("/") + "/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + self.env["BAILIAN_API_KEY"]})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read())
        except Exception as e:
            status, err = "error", "%s: %s" % (type(e).__name__, str(e)[:200])
        latency = round(time.time() - t0, 2)
        usage = (data.get("usage") or {}) if status == "ok" else {}
        cost = ((usage.get("prompt_tokens", 0) * PRICE["in"]
                 + usage.get("completion_tokens", 0) * PRICE["out"]) / 1e6
                if status == "ok" and "prompt_tokens" in usage and "completion_tokens" in usage else None)
        try:
            text = data["choices"][0]["message"].get("content") or ""
            finish = data["choices"][0].get("finish_reason")
        except Exception:
            text, finish = "", None
        parsed, perr = self._parse_response(text)
        rec = {"run_id": self.run_id, "arm": arm, "sid": sid,
               "request_scope": request_scope,
               "response_mode": self.response_mode,
               "call_id": call_id, "purpose": purpose, "fingerprint": fp,
               "reused": False, "model_requested": MODEL,
               "model_returned": data.get("model"), "status": status,
               "error": err, "finish_reason": finish,
               "latency_s": latency, "usage": usage,
               "cost_cny": (round(cost, 6) if cost is not None else None), "prompt_text": prompt,
               "image_path": (os.path.relpath(image_path, C.ROOT)
                              if image_path else None),
               "image_sha256": image_sha, "response_text": text,
               "json_parse_error": perr}
        self.records.append(rec)
        self.batch["real_requests"] += 1
        self.q_count[(arm, sid)] = self.q_count.get((arm, sid), 0) + 1
        self.arm_total[arm] = self.arm_total.get(arm, 0) + 1
        if status == "ok" and (parsed or self.response_mode == "text"):
            self.cache[fp] = {"usage": usage, "response_text": text,
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
            "note": "真实调用 call_id=%s" % call_id,
            "call_id": call_id, "status": status, "cost_cny": (round(cost, 6) if cost is not None else None),
            "usage": usage, "latency_s": latency, "error": err,
            # _flush 会清空 self.records；原文必须随 meta 返回，否则调用方
            # 只能拿到 None，文本型基线（SSC）会永远解析成“未知”。
            "response_text": text,
            "finish_reason": finish}

    def _flush(self):
        os.makedirs(self.rec_dir, exist_ok=True)
        with open(self.calls_path, "a", encoding="utf-8") as fh:
            for r in self.records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.records = []
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.cache, fh, ensure_ascii=False)
        os.replace(tmp, self.cache_path)

