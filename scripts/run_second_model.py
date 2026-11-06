"""Run the second-model replication (DeepSeek-V4.1-Flash or GLM-4.6V).

Two datasets, every arm the e2e stage defines (DIRECT, REREAD2, SSC, SSC-EVID).
The point is a different model family, not a different pipeline: prompts, branches,
quotas and scoring are byte-identical to the paper's run.

    python scripts/run_second_model.py --provider deepseek --check
    python scripts/run_second_model.py --provider deepseek --shards 6
    python scripts/run_second_model.py --provider deepseek --report

Provider differences that matter
--------------------------------
* DeepSeek enables chain-of-thought **by default**. With thinking on, the trace is
  billed as output and ``content`` can come back empty once ``max_tokens`` is
  reached, so it is disabled explicitly via ``{"thinking": {"type": "disabled"}}``.
* DeepSeek ignores ``temperature`` while thinking; with thinking disabled it applies.
* DeepSeek's image budget is capped at 1024 tokens per image (resized to roughly
  1300x1300). That is the same order as the paper's backbone, so the comparison is
  not confounded by visual resolution — but it should still be reported.
* DeepSeek bills peak/off-peak (2x during 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri,
  i.e. Beijing 09:00-12:00 and 14:00-18:00). Run the batch outside those windows.

Namespace
---------
``freeze.json`` records the model, so a second model cannot share a dataset
directory with the first. This script creates ``<dataset>_<tag>`` copies that share
the same images and gold, differing only in the model the freeze binds to.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def dataset_names():
    """Datasets to run; overridable with --datasets / EVID_DATASETS."""
    return [x.strip() for x in
            os.environ.get("EVID_DATASETS", "mixed400,numeric400").split(",") if x.strip()]


STAGE = "e2e"

PROVIDERS = {
    "bailian-qwen": {
        "tag": "qwen",
        "model": "qwen3.8-flash",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "send_thinking": "1",
        "enable_thinking": "0",
        "thinking_block": "",
        "price_offpeak": (0.8, 2.7),
        "price_peak": (0.8, 2.7),
        "note": "论文主骨干；与 D10 主实验同模型同端点",
    },
    "bailian-deepseek": {
        "tag": "dsflash",
        "model": "deepseek-v4.1-flash-1017",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "send_thinking": "1",       # DashScope compatible-mode uses the boolean form
        "enable_thinking": "0",
        "thinking_block": "",
        "price_offpeak": (1.0, 4.0),      # CNY / 1M tokens, input / output
        "price_peak": (1.0, 4.0),
        "note": "百炼托管；若 pilot 出现空 content 或输出 token 异常偏高，"
                "说明思考模式没关掉，改用 --provider deepseek（原生端点 thinking 块）",
    },
    "deepseek": {
        "tag": "dsflash",
        "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "send_thinking": "0",
        "thinking_block": "disabled",
        "price_offpeak": (1.0, 4.0),
        "price_peak": (2.0, 8.0),
        "note": "原生端点；图像上限 1024 token；必须显式关闭思考",
    },
    "glm": {
        "tag": "glm46v",
        "model": "glm-4.6v",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "send_thinking": "0",
        "thinking_block": "",
        "price_offpeak": (1.0, 3.0),
        "price_peak": (1.0, 3.0),
        "note": "不支持 structured output，JSON 解析失败率需单列",
    },
}


# ------------------------------------------------------------------ helpers
def load_env_file():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def is_peak():
    """DeepSeek peak: Mon-Fri 01:00-04:00 and 06:00-10:00 UTC."""
    now = datetime.now(timezone.utc)
    return now.weekday() < 5 and (1 <= now.hour < 4 or 6 <= now.hour < 10)


def apply_provider(name):
    spec = PROVIDERS[name]
    os.environ["EVID_MODEL"] = spec["model"]
    os.environ["EVID_BASE_URL"] = spec["base_url"]
    os.environ["EVID_SEND_THINKING"] = spec["send_thinking"]
    os.environ["EVID_ENABLE_THINKING"] = spec.get("enable_thinking", "0")
    os.environ["EVID_THINKING_BLOCK"] = spec["thinking_block"]
    peak = is_peak() and name == "deepseek"
    price_in, price_out = spec["price_peak"] if peak else spec["price_offpeak"]
    os.environ["EVID_PRICE_INPUT"] = str(price_in)
    os.environ["EVID_PRICE_OUTPUT"] = str(price_out)
    return spec, peak


def namespaces(tag, pilot=None):
    suffix = tag if pilot is None else "%s_pilot%d" % (tag, pilot)
    return {base: "%s_%s" % (base, suffix) for base in dataset_names()}


def ensure_namespace(base, tag, limit=None, pilot=None):
    suffix = tag if pilot is None else "%s_pilot%d" % (tag, pilot)
    source = ROOT / "datasets" / base
    target = ROOT / "datasets" / ("%s_%s" % (base, suffix))
    if (target / "runtime.jsonl").exists():
        return target
    target.mkdir(parents=True, exist_ok=True)

    runtime = [json.loads(x) for x in (source / "runtime.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    gold = {r["sample_id"]: r for r in
            (json.loads(x) for x in (source / "eval.jsonl").read_text(encoding="utf-8").splitlines() if x.strip())}
    if limit:
        runtime = runtime[:limit]

    with open(target / "runtime.jsonl", "w", encoding="utf-8") as handle:
        for record in runtime:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    with open(target / "eval.jsonl", "w", encoding="utf-8") as handle:
        for record in runtime:
            handle.write(json.dumps(gold[record["sample_id"]], ensure_ascii=False) + "\n")
    shutil.copyfile(source / "manifest.json", target / "manifest.json")

    print("  新建命名空间 %s（%d 题，与 %s 同题同图，仅模型不同）"
          % (target.name, len(runtime), base))
    return target


def preflight(provider):
    spec = PROVIDERS[provider]
    problems = []
    if not (os.environ.get("EVID_API_KEY") or os.environ.get("BAILIAN_API_KEY")):
        problems.append("缺少 EVID_API_KEY（写入仓库根目录 .env）")

    for base in dataset_names():
        runtime = ROOT / "datasets" / base / "runtime.jsonl"
        if not runtime.exists():
            problems.append("缺少 %s" % runtime)
            continue
        records = [json.loads(x) for x in runtime.read_text(encoding="utf-8").splitlines() if x.strip()]
        missing = [r["image"] for r in records if not (ROOT / r["image"]).exists()]
        if missing:
            problems.append("%s: %d/%d 张图片不存在，例如 %s" % (base, len(missing), len(records), missing[0]))

    if problems:
        print("preflight 未通过：")
        for item in problems:
            print("  - " + item)
        return False
    print("preflight 通过：%s / %s，%d 个数据集图片齐全" % (provider, spec["model"], len(dataset_names())))
    print("  注意：%s" % spec["note"])
    return True


def run_quiet(command, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log:
        return subprocess.call(command, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)


def freeze_all(tag, pilot=None):
    for base, name in namespaces(tag, pilot).items():
        ensure_namespace(base, tag, limit=pilot, pilot=pilot)
        if (ROOT / "runs" / name / "freeze.json").exists():
            print("  [skip] %s 已冻结" % name)
            continue
        code = run_quiet([sys.executable, "-m", "repro.runner", "freeze", "--dataset", name],
                         ROOT / "runs" / name / "freeze.log")
        print("  freeze %-24s exit=%d" % (name, code))
        if code != 0:
            return False
    return True


def run_shards(tag, shards, parallelism, pilot=None):
    jobs = []
    for name in namespaces(tag, pilot).values():
        log_dir = ROOT / "runs" / name / STAGE
        log_dir.mkdir(parents=True, exist_ok=True)
        jobs += [(name, i, log_dir / ("shard%d.log" % i)) for i in range(shards)]

    print("  %d 个分片，并发 %d" % (len(jobs), parallelism))
    pending, running, started, failures = list(jobs), [], time.time(), []
    while pending or running:
        while pending and len(running) < parallelism:
            name, index, log = pending.pop(0)
            handle = open(log, "a", encoding="utf-8")
            process = subprocess.Popen(
                [sys.executable, "scripts/run_experiment.py", "--dataset", name,
                 "--stage", STAGE, "--shard", "%d/%d" % (index, shards), "--authorize-paid"],
                cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT)
            running.append((name, index, process, handle))
        time.sleep(0.5)
        for item in list(running):
            name, index, process, handle = item
            if process.poll() is not None:
                running.remove(item)
                handle.close()
                if process.returncode != 0:
                    failures.append((name, index, process.returncode))
                done = len(jobs) - len(pending) - len(running)
                print("  [%d/%d] %s shard %d exit=%d  %.1f min"
                      % (done, len(jobs), name, index, process.returncode,
                         (time.time() - started) / 60), flush=True)
    return failures


def merge_all(tag, shards, pilot=None):
    for name in namespaces(tag, pilot).values():
        code = run_quiet([sys.executable, "scripts/run_experiment.py", "--dataset", name,
                          "--stage", STAGE, "--merge", "--shards", str(shards)],
                         ROOT / "runs" / name / STAGE / "merge.log")
        print("  merge %-24s exit=%d" % (name, code))


def report(tag, pilot=None):
    tokens = {"prompt": 0, "completion": 0}
    cost, calls, parse_fail, empty = 0.0, 0, 0, 0
    by_arm = {}
    for name in namespaces(tag, pilot).values():
        for ledger in sorted((ROOT / "runs" / name / STAGE).glob("*_calls.jsonl")):
            for line in ledger.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("reused"):
                    continue
                calls += 1
                usage = record.get("usage") or {}
                tokens["prompt"] += usage.get("prompt_tokens", 0)
                tokens["completion"] += usage.get("completion_tokens", 0)
                cost += record.get("cost_cny") or 0.0
                arm = record.get("arm") or "?"
                bucket = by_arm.setdefault(arm, {"calls": 0, "fail": 0, "empty": 0, "out": 0})
                bucket["calls"] += 1
                bucket["out"] += usage.get("completion_tokens", 0)
                if not (record.get("response_text") or "").strip():
                    empty += 1
                    bucket["empty"] += 1
                if record.get("json_parse_error"):
                    parse_fail += 1
                    bucket["fail"] += 1

    print("请求 %d　输入 %d token　输出 %d token　估算 ¥%.2f"
          % (calls, tokens["prompt"], tokens["completion"], cost))
    print("JSON 解析失败 %d（%.1f%%）——这个数字要进论文"
          % (parse_fail, 100.0 * parse_fail / max(calls, 1)))
    print("空响应 %d（%.1f%%）——若非零，多半是思考模式没关掉"
          % (empty, 100.0 * empty / max(calls, 1)))
    for arm, bucket in sorted(by_arm.items()):
        print("  %-12s 调用 %5d　空 %4d　解析失败 %4d（%.1f%%）　平均输出 %.0f token"
              % (arm, bucket["calls"], bucket["empty"], bucket["fail"],
                 100.0 * bucket["fail"] / max(bucket["calls"], 1),
                 bucket["out"] / max(bucket["calls"], 1)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--shards", type=int, default=6)
    parser.add_argument("--parallelism", type=int, default=6)
    parser.add_argument("--no-freeze", action="store_true")
    parser.add_argument("--pilot", type=int, default=None,
                        help="只跑前 N 题，用来验证接口形状与费用，约 ¥1")
    parser.add_argument("--arms", default="ssc,evid",
                        help="只跑这些臂（默认 ssc,evid；留空字符串表示全部四臂）")
    parser.add_argument("--variant", default="36", choices=["36", "37", "38", "39"],
                        help="提示词契约版本：36 论文版，37 裸值，38 保单位去叙述，39 加作答类型与精度")
    parser.add_argument("--tag", default=None,
                        help="自定义结果命名后缀（默认 dsflash，37 版自动为 dsflash37）")
    parser.add_argument("--datasets", default=None,
                        help="逗号分隔的数据集名（默认 mixed400,numeric400；中文集用 zh_tableeval）")
    args = parser.parse_args()

    load_env_file()
    if args.datasets:
        os.environ["EVID_DATASETS"] = args.datasets
    os.environ["EVID_E2E_ARMS"] = args.arms.strip()
    os.environ["EVID_PROMPT_VARIANT"] = args.variant
    spec, peak = apply_provider(args.provider)
    spec = dict(spec, tag=args.tag or (spec["tag"] + ("" if args.variant == "36" else args.variant)))

    if args.report:
        report(spec["tag"], args.pilot)
        return 0
    if args.check:
        return 0 if preflight(args.provider) else 1
    if not preflight(args.provider):
        return 1

    print("模型 %s　价格 ¥%.0f/¥%.0f 每百万 token（%s）"
          % (spec["model"], float(os.environ["EVID_PRICE_INPUT"]),
             float(os.environ["EVID_PRICE_OUTPUT"]), "高峰" if peak else "低谷"))
    print("执行臂：%s（其余臂不记录，不作零值参与比较）" % (args.arms.strip() or "全部四臂"))
    print("提示词契约：%s 版（结果目录后缀 %s）" % (args.variant, spec["tag"]))
    shards = 4 if args.pilot else args.shards
    if not args.no_freeze and not freeze_all(spec["tag"], args.pilot):
        return 1
    failures = run_shards(spec["tag"], shards, min(args.parallelism, shards), args.pilot)
    if failures:
        print("有分片失败：%s" % failures)
        return 1
    merge_all(spec["tag"], shards, args.pilot)
    report(spec["tag"], args.pilot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
