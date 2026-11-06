"""Score the two TableVQA-Bench subsets with their *native* answer rules.

vwtqsyn250: short answers, scored by the frozen scorer unchanged.

vtabfact250: the gold labels are "0"/"1" but the models answer
"True"/"False"/"yes"/"no".  The frozen string comparison would score every
item wrong, so a symmetric boolean normalisation is applied to BOTH arms
(gold and predictions).  This is an evaluation-format fix, not a method
change: it is identical for SSC and EVID and cannot favour either arm.

Also reports the majority-class baseline and per-class recall, because a
binary task with a 52% majority class can show a meaningless 3-point gap.

Usage:
    python scripts/score_tablevqa_holdout.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repro import common as C, evaluate  # noqa: E402

TRUE = {"1", "true", "yes", "t", "y", "correct", "entailed"}
FALSE = {"0", "false", "no", "f", "n", "incorrect", "refuted"}


def as_bool(text):
    s = str(text or "").strip().lower().rstrip(".! ")
    if s in TRUE:
        return "1"
    if s in FALSE:
        return "0"
    return None


def load(ns):
    results = C.folder(ns, "e2e") / "results.jsonl"
    records = [json.loads(x) for x in results.read_text(encoding="utf-8").splitlines() if x.strip()]
    gold = {r["sample_id"]: r for r in C.rows(C.dataset_path(ns) / "eval.jsonl")}
    runtime = {r["sample_id"]: r for r in C.rows(C.dataset_path(ns) / "runtime.jsonl")}
    return records, gold, runtime


def report(ns, boolean=False):
    records, gold, runtime = load(ns)
    print("\n%s  n=%d" % (ns, len(records)))

    def label(sid, answer):
        if not boolean:
            return evaluate.label(sid, answer, gold)
        want = as_bool(gold[sid]["answer"])
        got = as_bool(answer)
        return "C" if want is not None and got == want else "W"

    labels = {arm: {r["sample_id"]: label(r["sample_id"], r["arms"][arm].get("answer"))
                    for r in records} for arm in ("SSC", "EVID")}
    if not boolean:
        frozen = labels
    else:
        frozen = {arm: {r["sample_id"]: evaluate.label(
            r["sample_id"], r["arms"][arm].get("answer"), gold) for r in records}
            for arm in ("SSC", "EVID")}
        print("  （冻结精确匹配，未归一：SSC %d / EVID %d 正确——金标 0/1 与 True/False 不可比）"
              % (sum(v == "C" for v in frozen["SSC"].values()),
                 sum(v == "C" for v in frozen["EVID"].values())))

    for arm in ("SSC", "EVID"):
        n = sum(v == "C" for v in labels[arm].values())
        print("  %-4s 正确 %3d / %d（%.1f%%）" % (arm, n, len(records), 100 * n / len(records)))
    p = evaluate.paired(labels["SSC"], labels["EVID"], runtime)
    print("  EVID−SSC 净 %+d（赢 %d / 输 %d）  McNemar p=%.4f  区间[%+.2f,%+.2f]pp"
          % (p["net"], p["gained"], p["lost"], p["mcnemar_raw"],
             100 * p["source_table_bootstrap95"][0], 100 * p["source_table_bootstrap95"][1]))

    if not boolean:
        return

    dist = Counter(as_bool(gold[r["sample_id"]]["answer"]) for r in records)
    majority = max(dist.values()) / len(records)
    print("  金标分布 %s  多数类基线 %.1f%%" % (dict(dist), 100 * majority))
    for cls in ("0", "1"):
        ids = [r["sample_id"] for r in records
               if as_bool(gold[r["sample_id"]]["answer"]) == cls]
        line = ["  类别 %s (n=%d) 召回:" % (cls, len(ids))]
        for arm in ("SSC", "EVID"):
            hit = sum(labels[arm][s] == "C" for s in ids)
            line.append("%s %.1f%%" % (arm, 100 * hit / len(ids)))
        print("  " + "  ".join(line).strip())
    for arm in ("SSC", "EVID"):
        cm = Counter((as_bool(gold[r["sample_id"]]["answer"]),
                      as_bool(r["arms"][arm].get("answer")) or "?") for r in records)
        print("  %-4s 混淆 (金标→预测): 金0→0 %d 金0→1 %d 金1→0 %d 金1→1 %d 无法判定 %d"
              % (arm, cm[("0", "0")], cm[("0", "1")], cm[("1", "0")], cm[("1", "1")],
                 sum(v for (g, a), v in cm.items() if a == "?")))


def main():
    report("vwtqsyn250_qwen", boolean=False)
    report("vtabfact250_qwen", boolean=True)
    print("\n注意：VWTQ-Syn 与 mixed400 共享 31.3% 表池，只能称「同表池新渲染」，"
          "不能称独立域；VTabFact 与 mixed400 表池零重叠，是新的表来源。")


if __name__ == "__main__":
    main()
