"""Chinese held-out set: frozen score vs. a symmetric format-robust reading.

Why this exists
---------------
The frozen scorer compares one string.  On Chinese questions both arms tend to
answer in full sentences ("该表格包含10行和9列" instead of the gold "行数：10",
or "GDP影响为正的国家包括…文莱…" instead of "文莱").  That mixes answer
*style* with answer *correctness*, exactly the failure mode seen on the
deepseek 36-contract run.

This script applies the same two repairs to BOTH arms and reports both
readings.  It is a sensitivity check only: it can turn a frozen miss into a
hit, never the reverse, and it never replaces the frozen scorer in the paper.

Repairs, attempted in order, only after the frozen verdict is not C:
  1. leading yes/no, or the first numeric value (sign inferred from a
     preceding Chinese/English decrease cue);
  2. if the gold has no digits and is at least 2 characters, accept when the
     normalised gold string appears anywhere in the normalised answer.

Usage:
    python scripts/zh_robust_score.py zh_tableeval_qwen
    python scripts/zh_robust_score.py zh_tableeval_qwen --by-task
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repro import common as C, evaluate, numeric_helpers as E  # noqa: E402
from format_robust_score import leading_value  # noqa: E402

NEG_CUES_ZH = ("下降", "减少", "降低", "亏损", "下滑", "跌", "负增长", "低于")
YESNO = re.compile(r"^(是|否|对|错|能|不能|正确|错误|有|没有)\b|^[是否]", re.I)


def candidates(answer):
    text = str(answer or "").strip()
    if not text:
        return []
    out = []
    head = YESNO.match(text)
    if head:
        out.append(head.group(0))
    hit = re.search(r"-?\d[\d,]*\.?\d*\s*%?", text)
    if hit:
        value = hit.group(0).replace(" ", "")
        prefix = text[:hit.start()]
        neg = any(cue in prefix.lower() for cue in
                  ("decrease", "decline", "drop", "lower", "loss", "down", "reduction"))
        neg = neg or any(cue in prefix for cue in NEG_CUES_ZH)
        if neg and not value.startswith("-"):
            value = "-" + value
        out.append(value)
    return out


def relaxed_label(sid, answer, gold):
    """Frozen verdict, then the repairs above; never downgrades a frozen hit."""
    frozen = evaluate.label(sid, answer, gold)
    if frozen == "C":
        return "C"
    for candidate in leading_value(answer) + candidates(answer):
        if candidate and evaluate.label(sid, candidate, gold) == "C":
            return "C"
    ref = str((gold.get(sid) or {}).get("answer") or "")
    norm_ref = E.norm(ref)
    values = leading_value(answer) + candidates(answer)
    # A gold such as "行数：10" has no display-number, so the frozen scorer
    # compares strings; accept when the first number in the answer equals the
    # number embedded in the gold.
    ref_num = E.num(ref)
    if ref_num is not None:
        for candidate in values:
            got = E.num(candidate)
            if got is not None and abs(got - ref_num) <= max(abs(ref_num) * 0.01, 1e-9):
                return "C"
    if norm_ref and len(norm_ref) >= 2 and not re.search(r"\d", norm_ref):
        norm_ans = E.norm(answer)
        # Only accept when the answer is essentially the entity itself.  A long
        # sentence that merely mentions the gold ("...虽然采矿业在...") is a
        # false positive; the audit in zh_stratify.py found such cases.
        if norm_ref in norm_ans and len(norm_ans) <= 2 * len(norm_ref) + 6:
            return "C"
    return frozen


def load(namespace):
    results = C.folder(namespace, "e2e") / "results.jsonl"
    records = [json.loads(x) for x in results.read_text(encoding="utf-8").splitlines() if x.strip()]
    gold = {r["sample_id"]: r for r in C.rows(C.dataset_path(namespace) / "eval.jsonl")}
    runtime = {r["sample_id"]: r for r in C.rows(C.dataset_path(namespace) / "runtime.jsonl")}
    return records, gold, runtime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("namespace")
    ap.add_argument("--by-task", action="store_true")
    args = ap.parse_args()

    records, gold, runtime = load(args.namespace)
    arms = ("SSC", "EVID")
    frozen, relaxed = {}, {}
    for arm in arms:
        frozen[arm] = {r["sample_id"]: evaluate.label(
            r["sample_id"], r["arms"][arm].get("answer"), gold) for r in records}
        relaxed[arm] = {r["sample_id"]: relaxed_label(
            r["sample_id"], r["arms"][arm].get("answer"), gold) for r in records}

    n = len(records)
    print("%s  n=%d" % (args.namespace, n))
    print("  %-6s %8s %8s %8s %8s" % ("臂", "主评分", "宽松", "表述损失", "宽松-主"))
    for arm in arms:
        f = sum(v == "C" for v in frozen[arm].values())
        r = sum(v == "C" for v in relaxed[arm].values())
        print("  %-6s %8d %8d %8d %+8d" % (arm, f, r, r - f, r - f))

    for name, labels in (("主评分", frozen), ("宽松", relaxed)):
        p = evaluate.paired(labels["SSC"], labels["EVID"], runtime)
        print("  %s：EVID−SSC 净 %+d（赢 %d / 输 %d）  McNemar p=%.4f  区间[%+.2f,%+.2f]pp"
              % (name, p["net"], p["gained"], p["lost"], p["mcnemar_raw"],
                 100 * p["source_table_bootstrap95"][0], 100 * p["source_table_bootstrap95"][1]))

    if args.by_task:
        groups = defaultdict(list)
        for r in records:
            groups[runtime[r["sample_id"]]["source"]["task"]].append(r["sample_id"])
        print("\n  %-14s %4s %8s %8s %8s %8s" % ("任务", "n", "SSC主", "EVID主", "SSC宽", "EVID宽"))
        for task, ids in sorted(groups.items()):
            print("  %-14s %4d %8d %8d %8d %8d"
                  % (task, len(ids),
                     sum(frozen["SSC"][s] == "C" for s in ids),
                     sum(frozen["EVID"][s] == "C" for s in ids),
                     sum(relaxed["SSC"][s] == "C" for s in ids),
                     sum(relaxed["EVID"][s] == "C" for s in ids)))


if __name__ == "__main__":
    main()
