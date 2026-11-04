"""Score the budget-matched independent-vote control against the EVID run.

For each question the target is EVID's own prompt+completion tokens (image tokens
included); the vote stops at the first checkpoint at or above it (GEQ) and keeps
the last one below it (LEQ). Both checkpoints come from one trajectory, so the
actual cost is counted once.
"""
import argparse
import collections
import json
import statistics

from . import common as C
from . import evaluate as EV
from . import official_eval as O
from . import runner as X

MODE = "pure"


def official_label(answer, reference):
    if answer is None:
        return "A"
    _, prediction = O.fintabnet_normalize(answer)
    _, gold = O.fintabnet_normalize(reference)
    return "C" if any(x == y for x in prediction for y in gold) else "W"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score the independent-vote control.")
    parser.add_argument("--datasets", nargs="+", default=["mixed400", "numeric400"])
    args = parser.parse_args(argv)
    report, long_rows, all_calls = {}, [], []
    for dataset in args.datasets:
        runtime = {r["sample_id"]: r for r in C.rows(C.dataset_path(dataset) / "runtime.jsonl")}
        gold = {r["sample_id"]: r for r in C.rows(C.dataset_path(dataset) / "eval.jsonl")}
        reference = {r["sample_id"]: r for r in C.rows(X.folder(dataset, "e2e") / "results.jsonl")}
        new = C.rows(X.folder(dataset, MODE) / "results.jsonl")
        if len(new) != len(runtime) or {r["sample_id"] for r in new} != set(runtime):
            raise ValueError("incomplete vote run for " + dataset)
        labels = {arm: {} for arm in ("EVID", "VOTE_LEQ", "VOTE_GEQ")}
        project = {arm: {} for arm in labels}
        for record in new:
            sid = record["sample_id"]
            answers = {"EVID": reference[sid]["arms"]["EVID"]["answer"],
                       "VOTE_LEQ": record["before"]["answer"],
                       "VOTE_GEQ": (record["after"] or {}).get("answer") or record["answer"]}
            for arm, answer in answers.items():
                project[arm][sid] = EV.label(sid, answer, gold)
                labels[arm][sid] = (official_label(answer, gold[sid]["answer"])
                                    if dataset == "fin250" else project[arm][sid])
            long_rows.append({"dataset": dataset, "sample_id": sid, **answers,
                              **{arm + "_label": labels[arm][sid] for arm in labels},
                              "target_tokens": record["target_tokens"],
                              "actual_tokens": record["actual_tokens"],
                              "reached": record["reached"],
                              "unique_valid_answers": len({v for v in record["answers"] if v}),
                              "all_answers": record["answers"]})
        logs = [dict(dataset=dataset, **r) for p in X.folder(dataset, MODE).glob("*_calls.jsonl")
                for r in C.rows(p)]
        all_calls += logs
        deviations = [(r["actual_tokens"] - r["target_tokens"]) / r["target_tokens"]
                      for r in new if r["target_tokens"] and r["reached"]]
        report[dataset] = {
            "n": len(runtime),
            "counts": {arm: dict(collections.Counter(v.values())) for arm, v in labels.items()},
            "project_counts": {arm: dict(collections.Counter(v.values())) for arm, v in project.items()},
            "comparison": EV.paired(labels["VOTE_GEQ"], labels["EVID"], runtime),
            "budget": {"target": sum(r["target_tokens"] for r in new),
                       "actual": sum(r["actual_tokens"] for r in new),
                       "reached": sum(r["reached"] for r in new),
                       "median_overshoot": statistics.median(deviations) if deviations else None},
            "usage": EV.usage(logs)}
    last = 0.0
    for rank, (_dataset, entry) in enumerate(sorted(report.items(), key=lambda x: x[1]["comparison"]["mcnemar_raw"])):
        last = max(last, min(1.0, entry["comparison"]["mcnemar_raw"] * (len(report) - rank)))
        entry["comparison"]["holm_p"] = last
    output = C.REPORTS / MODE
    output.mkdir(parents=True, exist_ok=True)
    X.dump(output / "results.json", {"datasets": report, "usage": EV.usage(all_calls)})
    EV.write_csv(output / "per_sample.csv", long_rows)
    print(json.dumps({d: r["counts"] for d, r in report.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
