"""Recompute every headline number from the frozen per-question tables.

No model, no images, no API key: this reads ``results/`` and checks the numbers
quoted in the paper (tables 2, 4, 5 and the vote control). It is the fastest way
for a reviewer to see that the reported tables and the shipped artefacts agree.

    python scripts/verify_reported_numbers.py
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
CHECKED = []


def read(relative):
    path = RESULTS / relative
    if not path.exists():
        raise SystemExit("missing %s" % path)
    with path.open(encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def check(name, expected, actual):
    CHECKED.append((name, expected, actual, expected == actual))


def counts(rows, key, value="C"):
    return sum(1 for row in rows if row[key] == value)


def e2e():
    for dataset, expected in {
            "mixed400": {"DIRECT": 305, "REREAD2": 313, "SSC": 321, "EVID": 350},
            "numeric400": {"DIRECT": 274, "REREAD2": 285, "SSC": 308, "EVID": 325}}.items():
        rows = read("%s/end_to_end.csv" % dataset)
        for arm, correct in expected.items():
            check("%s e2e %s" % (dataset, arm), correct,
                  counts([r for r in rows if r["arm"] == arm], "label"))
    # source splits quoted in table 2a
    rows = read("numeric400/end_to_end.csv")
    for arm, correct in {"DIRECT": 169, "SSC": 168, "EVID": 187}.items():
        check("numeric400 financial200 %s" % arm, correct,
              counts([r for r in rows if r["arm"] == arm and r["source"] == "fintabnetqa"], "label"))
    for arm, correct in {"DIRECT": 105, "SSC": 140, "EVID": 138}.items():
        check("numeric400 finqa200 %s" % arm, correct,
              counts([r for r in rows if r["arm"] == arm and r["source"] == "finqa_rendered"], "label"))
    rows = read("mixed400/end_to_end.csv")
    for arm, correct in {"DIRECT": 147, "SSC": 141, "EVID": 162}.items():
        check("mixed400 financial174 %s" % arm, correct,
              counts([r for r in rows if r["arm"] == arm and r["source"] == "fintabnetqa"], "label"))
    for arm, correct in {"DIRECT": 158, "SSC": 180, "EVID": 188}.items():
        check("mixed400 vwtq226 %s" % arm, correct,
              counts([r for r in rows if r["arm"] == arm and r["source"] == "vwtq"], "label"))


def repair():
    for dataset, expected in {"mixed400": {"SSC": 341, "EVID": 346},
                              "numeric400": {"SSC": 316, "EVID": 325}}.items():
        rows = [r for r in read("%s/repair_rounds.csv" % dataset) if r["round"] == "2"]
        for arm, correct in expected.items():
            check("%s repair r2 %s" % (dataset, arm), correct,
                  counts([r for r in rows if r["arm"] == arm], "label"))
    # common-initial -> final transitions quoted in table 4 (round 2 rows)
    for dataset, expected in {"mixed400": {"SSC": (323, 22, 4), "EVID": (323, 27, 4)},
                              "numeric400": {"SSC": (301, 18, 3), "EVID": (301, 26, 2)}}.items():
        rows = [r for r in read("%s/repair_rounds.csv" % dataset) if r["round"] == "2"]
        for arm, (initial_correct, gained, lost) in expected.items():
            subset = [r for r in rows if r["arm"] == arm]
            check("%s repair %s initial correct" % (dataset, arm), initial_correct,
                  sum(1 for r in subset if r["initial_label"] == "C"))
            check("%s repair %s W->C" % (dataset, arm), gained,
                  sum(1 for r in subset if r["initial_label"] == "W" and r["label"] == "C"))
            check("%s repair %s C->W" % (dataset, arm), lost,
                  sum(1 for r in subset if r["initial_label"] == "C" and r["label"] == "W"))


def fin250():
    rows = read("fin250/predictions.csv")
    for arm, relieved, exact in (("REREAD2", 247, 225), ("EVID", 246, 231)):
        subset = [r for r in rows if r["arm"] == arm]
        check("fin250 %s relieved" % arm, relieved, sum(int(r["official_relief"]) for r in subset))
        check("fin250 %s exact" % arm, exact, sum(int(r["official_exact"]) for r in subset))


def vote():
    rows = read("pure/per_sample.csv")
    for dataset, expected in {"mixed400": (350, 304), "numeric400": (325, 280),
                              "fin250": (246, 245)}.items():
        subset = [r for r in rows if r["dataset"] == dataset]
        check("%s vote EVID" % dataset, expected[0], counts(subset, "EVID_label"))
        check("%s vote GEQ" % dataset, expected[1], counts(subset, "VOTE_GEQ_label"))


def ablation():
    rows = read("ablation/per_sample.csv")
    for dataset, expected in {
            "mixed400": {"FULL": 352, "NO_FACT_FIRST": 350, "GENERIC_ACQ": 354,
                         "NO_ACQ": 352, "NO_GATE": 354},
            "numeric400": {"FULL": 327, "NO_FACT_FIRST": 322, "GENERIC_ACQ": 326,
                           "NO_ACQ": 327, "NO_GATE": 326}}.items():
        subset = [r for r in rows if r["dataset"] == dataset]
        for arm, correct in expected.items():
            check("%s ablation %s" % (dataset, arm), correct,
                  counts([r for r in subset if r["arm"] == arm], "label"))


def main():
    e2e()
    repair()
    fin250()
    vote()
    ablation()
    width = max(len(name) for name, *_ in CHECKED)
    failures = 0
    for name, expected, actual, ok in CHECKED:
        if not ok:
            failures += 1
        print("%-*s  paper=%-5s computed=%-5s %s" % (width, name, expected, actual,
                                                     "ok" if ok else "MISMATCH"))
    print("\n%d checks, %d mismatches" % (len(CHECKED), failures))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
