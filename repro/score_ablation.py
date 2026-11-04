"""Score the five-arm ablation: accuracy per arm, paired exact McNemar vs FULL, Holm.

The paper pre-registered 4 deletions x 2 question sets = 8 comparisons and applied
Holm over that family. Pass both datasets to get the family-wide correction; a
single dataset gets its own family of four.
"""
import argparse
import collections
import json

from . import common as C
from . import runner as X
from . import scoring
from .evaluate import paired, write_csv

MODE = "ablation"


def labels(dataset):
    runtime = {r["sample_id"]: r for r in C.rows(C.dataset_path(dataset) / "runtime.jsonl")}
    gold = {r["sample_id"]: r for r in C.rows(C.dataset_path(dataset) / "eval.jsonl")}
    results = C.rows(X.folder(dataset, MODE) / "results.jsonl")
    if len(results) != len(runtime) or {r["sample_id"] for r in results} != set(runtime):
        raise ValueError("incomplete ablation run for " + dataset)
    table = collections.defaultdict(dict)
    rows = []
    for record in results:
        sid = record["sample_id"]
        for arm, outcome in record["arms"].items():
            answer = outcome.get("answer")
            if answer is None or str(answer).strip() == "":
                tag = "A"
            else:
                result = scoring.eval_one(sid, answer, gold[sid])
                tag = "C" if result["display_match"] or result["exec_match"] else "W"
            table[arm][sid] = tag
            rows.append({"dataset": dataset, "sample_id": sid, "arm": arm, "answer": answer,
                         "status": outcome.get("status"), "label": tag,
                         "calls": outcome.get("calls")})
    return runtime, table, rows


def holm(comparisons):
    last = 0.0
    ordered = sorted(comparisons.items(), key=lambda item: item[1]["mcnemar_raw"])
    for rank, (_key, value) in enumerate(ordered):
        last = max(last, min(1.0, value["mcnemar_raw"] * (len(ordered) - rank)))
        value["holm_p"] = last


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score the module ablation.")
    parser.add_argument("--datasets", nargs="+", default=["mixed400", "numeric400"])
    args = parser.parse_args(argv)
    report, summary, per_sample, comparisons = {}, [], [], {}
    for dataset in args.datasets:
        runtime, table, rows = labels(dataset)
        per_sample += rows
        arms = sorted(table)
        if "FULL" not in table:
            raise ValueError("FULL arm missing in " + dataset)
        entry = {"n": len(runtime), "arms": {}}
        for arm in arms:
            counts = collections.Counter(table[arm].values())
            entry["arms"][arm] = {"correct": counts["C"], "wrong": counts["W"],
                                  "unsubmitted": counts["A"],
                                  "accuracy": counts["C"] / len(runtime),
                                  "calls": sum(r["calls"] for r in rows if r["arm"] == arm)}
            summary.append({"dataset": dataset, "arm": arm, **entry["arms"][arm]})
        for arm in arms:
            if arm == "FULL":
                continue
            comparison = paired(table["FULL"], table[arm], runtime)
            comparison["dataset"] = dataset
            comparison["arm"] = arm
            comparison["delta_correct"] = (entry["arms"]["FULL"]["correct"]
                                           - entry["arms"][arm]["correct"])
            comparisons["%s:%s" % (dataset, arm)] = comparison
        report[dataset] = entry
    holm(comparisons)
    output = C.REPORTS / MODE
    output.mkdir(parents=True, exist_ok=True)
    X.dump(output / "metrics.json", {"datasets": report, "comparisons": comparisons,
                                     "note": "FULL is the reference; delta_correct = FULL - arm"})
    write_csv(output / "summary.csv", summary)
    write_csv(output / "per_sample.csv", per_sample)
    write_csv(output / "comparisons.csv", [dict(key=key, **value) for key, value in comparisons.items()])
    print(json.dumps({"accuracy": {d: {a: round(v["accuracy"], 4) for a, v in r["arms"].items()}
                                   for d, r in report.items()},
                      "comparisons": {k: {"net": v["net"], "holm_p": v["holm_p"]}
                                      for k, v in comparisons.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
