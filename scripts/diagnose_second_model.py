"""Why does the same method lose so much on another backbone?

Decomposes the SSC vs SSC-EVID gap into four layers that can be measured
separately:

  1. 初态 (fact-first initial answer)      -- is the first answer worse?
  2. 门禁 (accept / keep / correct)        -- does the loop repair or freeze the error?
  3. 作答格式 (answer string vs gold)      -- how much of a miss is verbosity?
  4. 题面类型 (numeric vs string gold)     -- where the format cost lands.

Usage: python scripts/diagnose_second_model.py
"""
import json
import statistics
import sys
from pathlib import Path
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repro import common as C  # noqa: E402
from repro import evaluate  # noqa: E402
from format_robust_score import relaxed_label  # noqa: E402

# point EVID_QWEN_ARCHIVE at a local copy of the internal 51 archive
QWEN = Path(os.environ.get("EVID_QWEN_ARCHIVE", "."))
CASES = {
    ("qwen3.8-flash", "mixed400"): (QWEN / "runs/mixed400/e2e/results.jsonl",
                                    QWEN / "datasets/mixed400/eval.jsonl"),
    ("qwen3.8-flash", "numeric400"): (QWEN / "runs/numeric400/e2e/results.jsonl",
                                      QWEN / "datasets/numeric400/eval.jsonl"),
    ("deepseek-v4.1-flash-1017", "mixed400"): (Path("runs/mixed400_dsflash/e2e/results.jsonl"),
                                               C.dataset_path("mixed400_dsflash") / "eval.jsonl"),
    ("deepseek-v4.1-flash-1017", "numeric400"): (Path("runs/numeric400_dsflash/e2e/results.jsonl"),
                                                 C.dataset_path("numeric400_dsflash") / "eval.jsonl"),
}


def load(results_path, eval_path):
    records = [json.loads(line) for line in open(results_path, encoding="utf-8")]
    gold = {r["sample_id"]: r for r in C.rows(eval_path)}
    runtime = {r["sample_id"]: r for r in C.rows(Path(eval_path).parent / "runtime.jsonl")}
    return records, gold, runtime


def stats(model, dataset):
    results_path, eval_path = CASES[(model, dataset)]
    records, gold, runtime = load(results_path, eval_path)
    out = {"n": len(records)}

    for arm in ("SSC", "EVID"):
        init = {(r["initials"].get(arm) or {}).get("answer") for r in records}
        del init
        i_frozen = [evaluate.label(r["sample_id"], (r["initials"].get(arm) or {}).get("answer"), gold)
                    for r in records if arm in r["initials"]]
        f_frozen = [evaluate.label(r["sample_id"], r["arms"][arm].get("answer"), gold)
                    for r in records if arm in r["arms"]]
        i_relax = [relaxed_label(r["sample_id"], (r["initials"].get(arm) or {}).get("answer"), gold)
                   for r in records if arm in r["initials"]]
        f_relax = [relaxed_label(r["sample_id"], r["arms"][arm].get("answer"), gold)
                   for r in records if arm in r["arms"]]
        lengths = [len(str(r["arms"][arm].get("answer") or "")) for r in records if arm in r["arms"]]
        out[arm] = {
            "initial_frozen": sum(v == "C" for v in i_frozen),
            "initial_relaxed": sum(v == "C" for v in i_relax),
            "final_frozen": sum(v == "C" for v in f_frozen),
            "final_relaxed": sum(v == "C" for v in f_relax),
            "miss_frozen": sum(v != "C" for v in f_frozen),
            "miss_recovered": sum(1 for a, b in zip(f_frozen, f_relax) if a != "C" and b == "C"),
            "len_median": statistics.median(lengths),
            "len_mean": round(statistics.mean(lengths), 1),
            "status": {},
            "false_accept": None,
            "repaired_from_wrong": None,
        }
        # status distribution, and what happens to a wrong initial answer
        wrong_initials = 0
        repaired = frozen_wrong = 0
        for r in records:
            if arm not in r["arms"]:
                continue
            st = r["arms"][arm].get("status")
            out[arm]["status"][st] = out[arm]["status"].get(st, 0) + 1
            sid = r["sample_id"]
            if evaluate.label(sid, (r["initials"].get(arm) or {}).get("answer"), gold) == "C":
                continue
            wrong_initials += 1
            if evaluate.label(sid, r["arms"][arm].get("answer"), gold) == "C":
                repaired += 1
            elif str(st).startswith("KEPT") or st in ("INITIAL", "REVIEW_ACCEPTED"):
                frozen_wrong += 1
        out[arm]["wrong_initials"] = wrong_initials
        out[arm]["repaired_from_wrong"] = repaired
        out[arm]["kept_wrong"] = frozen_wrong

    # evidence volume of the fact-first initial
    rows, usable, ops = [], [], []
    for r in records:
        ev = r["initials"].get("EVID") or {}
        rows.append(len(ev.get("evidence") or []))
        meta = ev.get("observation_meta") or {}
        if "usable_observation_count" in meta:
            usable.append(meta["usable_observation_count"])
        ops.append(len(ev.get("computed_aux") or []))
    out["evidence_rows_mean"] = round(statistics.mean(rows), 2) if rows else None
    out["evidence_rows_median"] = statistics.median(rows) if rows else None
    out["usable_mean"] = round(statistics.mean(usable), 2) if usable else None
    out["operations_mean"] = round(statistics.mean(ops), 2) if ops else None

    # per source: frozen / relaxed
    for arm in ("SSC", "EVID"):
        out[arm]["by_source"] = {}
        for source in sorted({x["dataset"] for x in runtime.values()}):
            ids = [r["sample_id"] for r in records if arm in r["arms"]
                   and runtime[r["sample_id"]]["dataset"] == source]
            out[arm]["by_source"][source] = {
                "n": len(ids),
                "frozen": sum(evaluate.label(s, r_["arms"][arm].get("answer"), gold) == "C"
                              for s in ids for r_ in records if r_["sample_id"] == s),
                "relaxed": sum(relaxed_label(s, r_["arms"][arm].get("answer"), gold) == "C"
                               for s in ids for r_ in records if r_["sample_id"] == s),
            }
    return out


def main():
    rows = []
    for (model, dataset) in CASES:
        s = stats(model, dataset)
        rows.append((model, dataset, s))
        print("=" * 78)
        print("%s / %s  n=%d  证据行 %.1f 条（中位 %s），可用观察 %s，辅助运算 %.1f"
              % (model, dataset, s["n"], s["evidence_rows_mean"],
                 s["evidence_rows_median"], s["usable_mean"], s["operations_mean"]))
        for arm in ("SSC", "EVID"):
            a = s[arm]
            print("  %-4s 初答 %3d/%3d  终答 %3d（归并 %3d）  失分 %3d 中可归并 %3d"
                  "  平均长度 %.1f  初答错 %d → 救回 %d / 保留错 %d"
                  % (arm, a["initial_frozen"], a["initial_relaxed"], a["final_frozen"],
                     a["final_relaxed"], a["miss_frozen"], a["miss_recovered"],
                     a["len_mean"], a["wrong_initials"], a["repaired_from_wrong"],
                     a["kept_wrong"]))
            print("       状态:", json.dumps(a["status"], ensure_ascii=False))
        print("  按来源（原判/归并）:",
              json.dumps({arm: s[arm]["by_source"] for arm in ("SSC", "EVID")},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
