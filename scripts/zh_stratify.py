"""Chinese held-out set: stratified analysis + audit of the relaxed scorer.

Stratification uses ONLY the dataset's own task labels, so it is fully
reproducible and involves no judgement about which items "should" favour the
method:

  A 表内定位   数值分析 + 信息查询 + 表格推理   (value/entity lookup in cells)
  B 长文本推理 数据分析                        (trends, causes, anomalies)
  C 结构题     表格结构理解                    (merged-cell questions that are
                                              void after grid rendering)

The relaxed scorer is audited by classifying every W->C repair and printing
samples, so the 104/104 reading is not an unexamined number.

Usage:
    python scripts/zh_stratify.py zh_tableeval_qwen
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repro import common as C, evaluate, numeric_helpers as E  # noqa: E402
from format_robust_score import leading_value  # noqa: E402
from zh_robust_score import candidates, relaxed_label  # noqa: E402

GROUPS = {
    "数值分析": "A 表内定位",
    "信息查询": "A 表内定位",
    "表格推理": "A 表内定位",
    "数据分析": "B 长文本推理",
    "表格结构理解": "C 结构题(渲染后失效)",
}


def repair_reason(sid, answer, gold):
    """Classify why the relaxed rule upgraded a frozen miss."""
    if evaluate.label(sid, answer, gold) == "C":
        return None
    text = str(answer or "")
    ref = str((gold.get(sid) or {}).get("answer") or "")
    for candidate in leading_value(answer) + candidates(answer):
        if candidate and evaluate.label(sid, candidate, gold) == "C":
            return "首值/是非词直接命中"
    ref_num = E.num(ref)
    if ref_num is not None:
        for candidate in leading_value(answer) + candidates(answer):
            got = E.num(candidate)
            if got is not None and abs(got - ref_num) <= max(abs(ref_num) * 0.01, 1e-9):
                return "金标内嵌数值等价"
    norm_ref = E.norm(ref)
    if norm_ref and len(norm_ref) >= 2 and not re.search(r"\d", norm_ref):
        if norm_ref in E.norm(answer):
            return "实体子串命中"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("namespace")
    args = ap.parse_args()

    ns = args.namespace
    results = C.folder(ns, "e2e") / "results.jsonl"
    records = [json.loads(x) for x in results.read_text(encoding="utf-8").splitlines() if x.strip()]
    gold = {r["sample_id"]: r for r in C.rows(C.dataset_path(ns) / "eval.jsonl")}
    runtime = {r["sample_id"]: r for r in C.rows(C.dataset_path(ns) / "runtime.jsonl")}
    arms = ("SSC", "EVID")

    frozen = {a: {r["sample_id"]: evaluate.label(
        r["sample_id"], r["arms"][a].get("answer"), gold) for r in records} for a in arms}
    relaxed = {a: {r["sample_id"]: relaxed_label(
        r["sample_id"], r["arms"][a].get("answer"), gold) for r in records} for a in arms}

    by_group = defaultdict(list)
    for r in records:
        task = runtime[r["sample_id"]]["source"]["task"]
        by_group[GROUPS.get(task, "? " + task)].append(r["sample_id"])

    print("%s  n=%d\n" % (ns, len(records)))
    print("%-22s %4s %7s %7s %7s %7s %7s %9s" %
          ("分层", "n", "SSC主", "EVID主", "净", "SSC宽", "EVID宽", "净(宽)"))
    for name in sorted(by_group):
        ids = by_group[name]
        sf = sum(frozen["SSC"][s] == "C" for s in ids)
        ef = sum(frozen["EVID"][s] == "C" for s in ids)
        sr = sum(relaxed["SSC"][s] == "C" for s in ids)
        er = sum(relaxed["EVID"][s] == "C" for s in ids)
        print("%-22s %4d %7d %7d %+7d %7d %7d %+9d" %
              (name, len(ids), sf, ef, ef - sf, sr, er, er - sr))

    print("\n分层显著性（主评分 / 宽松）")
    for name in sorted(by_group):
        ids = by_group[name]
        line = [name]
        for labels in (frozen, relaxed):
            p = evaluate.paired({s: labels["SSC"][s] for s in ids},
                                {s: labels["EVID"][s] for s in ids},
                                {s: runtime[s] for s in ids})
            line.append("净%+d p=%.3f" % (p["net"], p["mcnemar_raw"]))
        print("  %-22s %-18s %-18s" % tuple(line))

    # ---- audit the relaxed scorer -------------------------------------
    print("\n宽松口径改判审计（只统计主评分判错的题）")
    reasons = Counter()
    samples = defaultdict(list)
    for r in records:
        sid = r["sample_id"]
        for arm in arms:
            if frozen[arm][sid] == "C":
                continue
            ans = r["arms"][arm].get("answer")
            reason = repair_reason(sid, ans, gold)
            if reason is None:
                continue
            reasons[reason] += 1
            if len(samples[reason]) < 4:
                samples[reason].append((sid, arm, gold[sid]["answer"], str(ans)[:70]))
    for reason, count in reasons.most_common():
        print("  %-20s %3d 题" % (reason, count))
        for s in samples[reason]:
            print("     %s %-4s 金标 %-16r 答 %r" % (s[0], s[1], s[2][:16], s[3]))

    upgrades = sum(reasons.values())
    changed = sum(1 for r in records for a in arms
                  if frozen[a][r["sample_id"]] != "C" and relaxed[a][r["sample_id"]] == "C")
    print("\n  改判总数 %d；可归因 %d；未解释 %d" % (changed, upgrades, changed - upgrades))


if __name__ == "__main__":
    main()
