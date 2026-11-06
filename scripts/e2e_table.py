"""Per-arm accuracy for a namespace that only ran the end-to-end stage.

The main scorer (`repro.evaluate`) needs the `initial` and `repair` stages as
well, which the second-model namespace does not have: only DIRECT, REREAD2,
SSC and EVID were executed there.  This script scores exactly those four arms
with the same label function, so the numbers are comparable with Table 4.

Usage:
    python scripts/e2e_table.py mixed400_dsflash numeric400_dsflash
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repro import common as C  # noqa: E402
from repro import evaluate  # noqa: E402
from repro import runner as X  # noqa: E402

ROLEMAP = {"DIRECT": ["DIRECT"], "REREAD2": ["REREAD2"],
           "SSC": ["SSC_INIT", "SSC"], "EVID": ["EVID_FACTS", "EVID_INIT", "EVID"]}


def score(dataset):
    out_dir = X.folder(dataset, "e2e")
    path = out_dir / "results.jsonl"
    if not path.exists():
        raise SystemExit("no merged results for %s (run --merge first)" % dataset)
    records = C.rows(path)
    runtime = {r["sample_id"]: r for r in C.rows(X.dataset_path(dataset) / "runtime.jsonl")}
    gold = {r["sample_id"]: r for r in C.rows(X.dataset_path(dataset) / "eval.jsonl")}
    ledger = [r for p in out_dir.glob("*_calls.jsonl") for r in C.rows(p)]
    n = len(runtime)
    if len(records) != n or {r["sample_id"] for r in records} != set(runtime):
        raise ValueError("incomplete denominator: %d/%d" % (len(records), n))

    labels, firsts, report = {}, {}, {"dataset": dataset, "n": n, "arms": {}}
    for arm, roles in ROLEMAP.items():
        final, first = {}, {}
        for record in records:
            sid = record["sample_id"]
            out = record["arms"].get(arm)
            if out is None:
                continue
            first[sid] = evaluate.label(sid, (record["initials"].get(arm) or {}).get("answer"), gold)
            final[sid] = evaluate.label(sid, out.get("answer"), gold)
        if not final:
            continue
        labels[arm], firsts[arm] = final, first
        cost = evaluate.usage([r for r in ledger if r.get("arm") in roles])
        stat = evaluate.transitions(first, final)
        stat["correct_final"] = sum(v == "C" for v in final.values())
        stat["accuracy"] = 100.0 * stat["correct_final"] / len(final)
        stat["correct_initial"] = sum(v == "C" for v in first.values())
        stat["initial_accuracy"] = 100.0 * stat["correct_initial"] / len(first)
        stat["by_source"] = {
            source: {"n": sum(1 for sid in final if runtime[sid]["dataset"] == source),
                     "correct": sum(1 for sid in final
                                    if runtime[sid]["dataset"] == source and final[sid] == "C")}
            for source in sorted({r["dataset"] for r in runtime.values()})}
        stat["cost"] = cost
        report["arms"][arm] = stat

    report["comparisons"] = {}
    for a, b in (("SSC", "EVID"), ("REREAD2", "SSC"), ("DIRECT", "EVID")):
        if a in labels and b in labels:
            report["comparisons"]["%s_vs_%s" % (a, b)] = evaluate.paired(
                labels[a], labels[b], runtime)
    return report


def main(argv):
    datasets = argv or ["mixed400_dsflash", "numeric400_dsflash"]
    reports = Path(C.ROOT) / "reports"
    reports.mkdir(exist_ok=True)
    out = {}
    for dataset in datasets:
        report = score(dataset)
        out[dataset] = report
        print("\n%s  n=%d" % (dataset, report["n"]))
        print("  %-8s %6s %6s %6s %8s" % ("arm", "首答", "终答", "净增", "准确率"))
        for arm, stat in report["arms"].items():
            net = stat["correct_final"] - stat["correct_initial"]
            print("  %-8s %6d %6d %+6d %7.2f%%"
                  % (arm, stat["correct_initial"], stat["correct_final"], net, stat["accuracy"]))
        for key, cmp in report["comparisons"].items():
            print("  %s: 净 %+d (得 %d / 失 %d)  McNemar p=%.4f  95%%CI [%+.2f, %+.2f]"
                  % (key, cmp["net"], cmp["gained"], cmp["lost"], cmp["mcnemar_raw"],
                     100 * cmp["source_table_bootstrap95"][0],
                     100 * cmp["source_table_bootstrap95"][1]))
        print("  按来源:", json.dumps({a: s["by_source"] for a, s in report["arms"].items()},
                                     ensure_ascii=False))
    path = reports / "second_model_e2e.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n写出", path)


if __name__ == "__main__":
    main(sys.argv[1:])
