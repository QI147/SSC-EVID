"""Official FinTabNetQA 250 scoring: relieved accuracy, exact match, and the frozen
project score reported alongside.

The official metric normalises both sides and accepts any matching variant
("1.2 million" == "1,200,000"), which is why the paper reports relieved and exact
separately and never calls relieved accuracy a unit-verification metric.
"""
import argparse
import collections
import json

from . import common as C
from . import evaluate as EV
from . import official_eval as O
from . import runner as X

MODE = "fin250"
ARMS = {"REREAD2": ["REREAD2"], "EVID": ["EVID_FACTS", "EVID_INIT", "EVID"]}


def official(answer, gold):
    if answer is None:
        return {"relieved": 0, "exact": 0}
    prediction, prediction_variants = O.fintabnet_normalize(str(answer))
    reference, reference_variants = O.fintabnet_normalize(str(gold))
    return {"relieved": int(any(x == y for x in prediction_variants for y in reference_variants)),
            "exact": int(prediction == reference)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score the FinTabNetQA 250 run.")
    parser.add_argument("--dataset", default="fin250")
    args = parser.parse_args(argv)
    dataset = args.dataset
    X.verify(dataset)
    runtime = {r["sample_id"]: r for r in C.rows(C.dataset_path(dataset) / "runtime.jsonl")}
    gold = {r["sample_id"]: r for r in C.rows(C.dataset_path(dataset) / "eval.jsonl")}
    outputs = C.rows(X.folder(dataset, MODE) / "results.jsonl")
    n = len(runtime)
    if len(outputs) != n or {r["sample_id"] for r in outputs} != set(runtime):
        raise ValueError("incomplete run")
    log = [r for p in X.folder(dataset, MODE).glob("*_calls.jsonl") for r in C.rows(p)]
    report = {"dataset": dataset, "n": n,
              "metrics": "official relieved + official exact; project display/exec separately",
              "arms": {}, "new_call_usage": EV.usage(log)}
    rows = []
    truth = {}
    for arm, roles in ARMS.items():
        scores, official_labels, project_labels, initials = [], {}, {}, {}
        for record in outputs:
            sid = record["sample_id"]
            answer = record["arms"][arm].get("answer")
            initial = (record["initials"].get(arm) or {}).get("answer")
            score = official(answer, gold[sid]["answer"])
            initial_score = official(initial, gold[sid]["answer"])
            scores.append(score)
            official_labels[sid] = "A" if answer is None else ("C" if score["relieved"] else "W")
            initials[sid] = "A" if initial is None else ("C" if initial_score["relieved"] else "W")
            project_labels[sid] = EV.label(sid, answer, gold)
            rows.append({"sample_id": sid, "qa_id": rt_id(runtime[sid]), "question": runtime[sid]["question"],
                         "arm": arm, "initial_answer": initial, "answer": answer,
                         "gold": gold[sid]["answer"], "official_relieved": score["relieved"],
                         "official_exact": score["exact"], "project_label": project_labels[sid],
                         "status": record["arms"][arm]["status"]})
        report["arms"][arm] = {
            "official_correct": sum(s["relieved"] for s in scores),
            "official_accuracy": sum(s["relieved"] for s in scores) / n,
            "official_exact_correct": sum(s["exact"] for s in scores),
            "project_counts": dict(collections.Counter(project_labels.values())),
            "official_transition": EV.transitions(initials, official_labels),
            "cost": EV.usage([r for r in log if r.get("arm") in roles])}
        truth[arm] = official_labels
    if len(truth) == 2:
        report["paired_EVID_minus_REREAD2"] = EV.paired(truth["REREAD2"], truth["EVID"], runtime)
    output = C.REPORTS / MODE
    output.mkdir(parents=True, exist_ok=True)
    X.dump(output / "metrics.json", report)
    EV.write_csv(output / "fin250_predictions.csv", rows)
    print(json.dumps({arm: {"relieved": v["official_correct"], "exact": v["official_exact_correct"]}
                      for arm, v in report["arms"].items()}, ensure_ascii=False))


def rt_id(record):
    source = record.get("source") or {}
    return source.get("qa_id") if isinstance(source, dict) else None


if __name__ == "__main__":
    main()
