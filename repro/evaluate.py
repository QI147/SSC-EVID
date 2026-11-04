"""Offline scoring: fixed denominator, UNKNOWN kept separate, shared cost accounted once.

This is the only module that reads ``eval.jsonl``. The runner refuses to run if
``scoring`` is imported in the model process, so gold never reaches a prompt.
"""
import argparse
import collections
import csv
import json
import math
import random

from . import common as C
from . import runner as X
from . import scoring

ROOT = C.ROOT


def label(sid, answer, gold):
    if answer is None or str(answer).strip() == "":
        return "A"
    result = scoring.eval_one(sid, answer, gold[sid])
    return "C" if result["display_match"] or result["exec_match"] else "W"


def ratio(a, b):
    return a / b if b else None


def mcnemar(gain, loss):
    n = gain + loss
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(gain, loss) + 1)) / (2 ** n)) if n else 1.0


def paired(a, b, runtime):
    ids = list(runtime)
    gain = sum(a[s] != "C" and b[s] == "C" for s in ids)
    loss = sum(a[s] == "C" and b[s] != "C" for s in ids)
    groups = collections.defaultdict(lambda: [0, 0])
    for s in ids:
        groups[(runtime[s]["dataset"], runtime[s]["source_group"])][0] += int(b[s] == "C") - int(a[s] == "C")
        groups[(runtime[s]["dataset"], runtime[s]["source_group"])][1] += 1
    strata = collections.defaultdict(list)
    for (source, _group), values in groups.items():
        strata[source].append(values)
    rng = random.Random(20261030)
    boot = []
    for _ in range(2000):
        drawn = [rng.choice(g) for g in strata.values() for _ in g]
        boot.append(sum(x[0] for x in drawn) / sum(x[1] for x in drawn))
    boot.sort()
    return {"n": len(ids), "gained": gain, "lost": loss, "net": gain - loss,
            "mcnemar_raw": mcnemar(gain, loss),
            "source_table_bootstrap95": [boot[50], boot[1949]]}


def ledger(dataset, mode):
    return [r for p in X.folder(dataset, mode).glob("*_calls.jsonl") for r in C.rows(p)]


def usage(entries):
    unique = [r for r in entries if not r.get("reused")]
    missing = sum("prompt_tokens" not in (r.get("usage") or {})
                  or "completion_tokens" not in (r.get("usage") or {}) for r in unique)
    known = round(sum(float(r.get("cost_cny") or 0) for r in unique), 6)
    return {"requests": len(unique),
            "input_tokens": sum((r.get("usage") or {}).get("prompt_tokens", 0) for r in unique),
            "output_tokens": sum((r.get("usage") or {}).get("completion_tokens", 0) for r in unique),
            "cost_cny_estimated": None if missing else known,
            "known_cost_subtotal_cny": known,
            "usage_missing_requests": missing,
            "finish": dict(collections.Counter(r.get("finish_reason") for r in unique)),
            "http_status": dict(collections.Counter(r.get("status") for r in unique))}


def transitions(initial, final):
    matrix = collections.Counter(initial[s] + "->" + final[s] for s in initial)
    nc = sum(v == "C" for v in initial.values())
    nw = sum(v == "W" for v in initial.values())
    fixed = len(initial)
    gained = matrix["W->C"]
    corrupted = matrix["C->W"]
    lost = matrix["C->A"]
    return {"n": fixed,
            "initial_counts": dict(collections.Counter(initial.values())),
            "final_counts": dict(collections.Counter(final.values())),
            "matrix": dict(matrix),
            "accuracy": sum(v == "C" for v in final.values()) / fixed,
            "corrected": gained, "corrupted": corrupted, "correct_lost_to_unsubmitted": lost,
            "repair_rate": ratio(gained, nw), "corruption_rate": ratio(corrupted, nc),
            "correct_loss_rate": ratio(corrupted + lost, nc),
            "correct_retention_rate": ratio(matrix["C->C"], nc),
            "net_repair_per_all": (gained - corrupted - lost) / fixed}


def detection(starts, records):
    counts = collections.Counter()
    classified_total = sum(x in ("C", "W") for x in starts.values())
    wrong_total = sum(x == "W" for x in starts.values())
    for sid, record in records.items():
        if starts[sid] == "A":
            counts["initial_unsubmitted"] += 1
            continue
        if not record.get("executed"):
            counts["not_executed"] += 1
            continue
        judgment = record.get("judgment")
        if judgment in ("correct", "incorrect"):
            counts["classified"] += 1
            if judgment == "incorrect":
                counts["TP" if starts[sid] == "W" else "FP"] += 1
            else:
                counts["TN" if starts[sid] == "C" else "FN"] += 1
        elif judgment == "unknown":
            counts["unknown"] += 1
        elif judgment == "not_applicable":
            counts["no_detection_protocol"] += 1
        else:
            counts["parse_or_method_failure"] += 1
    return {**dict(counts),
            "error_recall": ratio(counts["TP"], wrong_total),
            "detection_precision": ratio(counts["TP"], counts["TP"] + counts["FP"]),
            "selective_accuracy": ratio(counts["TP"] + counts["TN"], counts["classified"]),
            "coverage": ratio(counts["classified"], classified_total),
            "unknown_rate": ratio(counts["unknown"], classified_total),
            "note": "UNKNOWN/unexecuted are not counted as right or wrong"}


def write_csv(path, records):
    if not records:
        return
    fields = list(dict.fromkeys(k for r in records for k in r))
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                             for k, v in record.items()})


def evaluate(dataset):
    X.verify(dataset)
    X.verify_initial(dataset)
    runtime = {r["sample_id"]: r for r in C.rows(X.dataset_path(dataset) / "runtime.jsonl")}
    gold = {r["sample_id"]: r for r in C.rows(X.dataset_path(dataset) / "eval.jsonl")}
    n = len(runtime)
    initial = C.rows(X.initial_path(dataset))
    e2e = C.rows(X.folder(dataset, "e2e") / "results.jsonl")
    repair = C.rows(X.folder(dataset, "repair") / "results.jsonl")
    for records in (initial, e2e, repair):
        if len(records) != n or {r["sample_id"] for r in records} != set(runtime):
            raise ValueError("incomplete or mismatched denominator")

    commons = {r["sample_id"]: r["initial"] for r in initial}
    initial_labels = {sid: label(sid, (v or {}).get("answer"), gold) for sid, v in commons.items()}
    common_cost = usage(ledger(dataset, "initial"))
    e2e_log = ledger(dataset, "e2e")
    fix_log = ledger(dataset, "repair")
    report = {"dataset": dataset, "n": n,
              "common_initial": dict(collections.Counter(initial_labels.values())),
              "common_cost": common_cost, "e2e": {}, "repair": {}, "comparisons": {},
              "scope": "development; natural errors only; shared initial costs not summed across arms"}
    end_rows, fix_rows, summary = [], [], []
    labels_e2e, labels_fix = {}, {}
    rolemap = {"DIRECT": ["DIRECT"], "REREAD2": ["REREAD2"],
               "SSC": ["SSC_INIT", "SSC"], "EVID": ["EVID_FACTS", "EVID_INIT", "EVID"]}

    for arm, roles in rolemap.items():
        final, first = {}, {}
        present = False
        for record in e2e:
            sid = record["sample_id"]
            out = record["arms"].get(arm)
            if out is None:
                continue
            present = True
            initial_answer = (record["initials"].get(arm) or {}).get("answer")
            first[sid] = label(sid, initial_answer, gold)
            final[sid] = label(sid, out.get("answer"), gold)
            end_rows.append({"dataset": dataset, "source": runtime[sid]["dataset"], "sample_id": sid,
                             "question": runtime[sid]["question"], "arm": arm,
                             "initial_answer": initial_answer, "answer": out.get("answer"),
                             "initial_label": first[sid], "label": final[sid],
                             "status": out["status"],
                             "trace_file": str(X.folder(dataset, "e2e") / "results.jsonl")})
        if not present:
            continue
        cost = usage([r for r in e2e_log if r.get("arm") in roles])
        stat = transitions(first, final)
        stat["cost"] = cost
        stat["by_source"] = {source: dict(collections.Counter(final[sid] for sid in runtime
                                                             if runtime[sid]["dataset"] == source))
                             for source in {r["dataset"] for r in runtime.values()}}
        report["e2e"][arm] = stat
        labels_e2e[arm] = final
        summary.append({"dataset": dataset, "experiment": "end_to_end", "arm": arm, "round": 0,
                        **{k: v for k, v in stat.items() if not isinstance(v, dict)},
                        **{k: v for k, v in cost.items() if not isinstance(v, dict)}})

    repair_plan = [("KEEP", None), ("SSC", "FIX_SSC"), ("EVID", "FIX_EVID"), ("RR_MATCH", "RR_MATCH")]
    for arm, role in repair_plan:
        if arm not in (repair[0]["arms"] if repair else {}):
            continue
        rounds, previous = {}, initial_labels
        for round_index in (1, 2):
            current, round_records = {}, {}
            for record in repair:
                sid = record["sample_id"]
                common = commons[sid]
                if record.get("common_sha256") and record["common_sha256"] != X.canonical(common):
                    raise ValueError("common mismatch")
                if not record["shared_unchanged"]:
                    raise ValueError("shared initial modified")
                records = record["arms"][arm].get("rounds", [])
                rec = records[round_index - 1] if len(records) >= round_index else \
                    {"answer": None, "executed": False, "judgment": "not_executed",
                     "status": "INITIAL_MISSING", "calls": 0}
                current[sid] = label(sid, rec.get("answer"), gold)
                round_records[sid] = rec
                trace = rec.get("trace", {})
                proposal = (trace.get("repair") or {}).get("answer")
                fix_rows.append({"dataset": dataset, "source": runtime[sid]["dataset"], "sample_id": sid,
                                 "question": runtime[sid]["question"], "arm": arm, "round": round_index,
                                 "common_answer": (common or {}).get("answer"),
                                 "common_reasoning": (common or {}).get("reasoning"),
                                 "answer": rec.get("answer"),
                                 "initial_label": initial_labels[sid],
                                 "round_start_label": previous[sid], "label": current[sid],
                                 "executed": rec.get("executed"),
                                 "model_judgment": rec.get("judgment"), "status": rec["status"],
                                 "calls": rec.get("calls", 0), "proposal": proposal,
                                 "proposal_label": label(sid, proposal, gold) if proposal is not None else None,
                                 "unresolved": trace.get("unresolved", []),
                                 "error_type": (trace.get("check")
                                                or (trace.get("events") or [{}])[0]).get("error_type"),
                                 "trace_file": str(X.folder(dataset, "repair") / "results.jsonl")})
            stat = transitions(initial_labels, current)
            stat["increment_from_previous_round"] = transitions(previous, current)
            stat["detection_current_round"] = detection(previous, round_records)
            stat["changed_wrong_to_wrong"] = sum(
                initial_labels[sid] == "W" and current[sid] == "W"
                and str(round_records[sid].get("answer")) != str((commons[sid] or {}).get("answer"))
                for sid in runtime)
            cost = usage([r for r in fix_log if r.get("arm") == role
                          and str(r.get("purpose", "")).startswith("r%d." % round_index)]) if role else usage([])
            stat["exclusive_round_cost"] = cost
            rounds["r%d" % round_index] = stat
            summary.append({"dataset": dataset, "experiment": "shared_initial_repair", "arm": arm,
                            "round": round_index,
                            **{k: v for k, v in stat.items() if not isinstance(v, dict)},
                            **{k: v for k, v in stat["detection_current_round"].items()
                               if not isinstance(v, (dict, list))},
                            **{k: v for k, v in cost.items() if not isinstance(v, dict)}})
            previous = current
        report["repair"][arm] = rounds
        labels_fix[arm] = previous

    if "SSC" in labels_e2e and "EVID" in labels_e2e:
        report["comparisons"]["primary_e2e_EVID_minus_SSC"] = paired(labels_e2e["SSC"], labels_e2e["EVID"], runtime)
    if "SSC" in labels_fix and "EVID" in labels_fix:
        report["comparisons"]["primary_repair_r2_EVID_minus_SSC"] = paired(labels_fix["SSC"], labels_fix["EVID"], runtime)
    if "RR_MATCH" in labels_fix and "EVID" in labels_fix:
        report["comparisons"]["repair_r2_EVID_minus_RR_MATCH"] = paired(labels_fix["RR_MATCH"], labels_fix["EVID"], runtime)

    output = C.REPORTS / dataset
    output.mkdir(parents=True, exist_ok=True)
    report["input_freeze"] = {"master": X.verify(dataset)["sha256"],
                              "results": {m: C.sha256_file(X.folder(dataset, m) / "results.jsonl")
                                          for m in ("initial", "e2e", "repair")}}
    X.dump(output / "metrics.json", report)
    write_csv(output / "summary.csv", summary)
    write_csv(output / "end_to_end.csv", end_rows)
    write_csv(output / "repair_rounds.csv", fix_rows)
    all_calls = [dict(dataset=dataset, mode=mode, **r) for mode in ("initial", "e2e", "repair")
                 for r in ledger(dataset, mode)]
    write_csv(output / "calls_full.csv", all_calls)
    X.dump(output / "tables.json", {"summary": summary, "end_to_end": end_rows, "repair_rounds": fix_rows})
    print(json.dumps({"dataset": dataset, "common_initial": report["common_initial"],
                      "e2e": {a: v["final_counts"] for a, v in report["e2e"].items()},
                      "repair_r2": {a: v["r2"]["final_counts"] for a, v in report["repair"].items()}},
                     ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score a frozen run against the gold file.")
    parser.add_argument("--dataset", required=True)
    args = parser.parse_args(argv)
    evaluate(args.dataset)


if __name__ == "__main__":
    main()
