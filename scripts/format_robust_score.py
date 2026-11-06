"""Frozen score versus a format-normalised score, applied identically to arms.

Motivation
----------
The frozen scorer compares the answer string after numeric normalisation.  A
longer answer that states the same value ("A decrease of $124 million (2015:
$1,666m; 2016: $1,542m)") fails that comparison even though the value is right.
On qwen3.8-flash both arms answered tersely, so this never mattered; on
deepseek-v4.1-flash-1017 the EVID prompt's "answer with units/names" contract
produces longer answers, and the frozen score then mixes answer quality with
answer style.

This script reports both readings.  The extraction below is deliberately crude
and applied to *every* arm and *every* model: leading yes/no, otherwise the
first number before the first parenthesis, with the sign taken from a preceding
"decrease/decline/drop/lower/loss" cue.  It is a sensitivity check, not a
replacement for the frozen scorer.

Usage:
    python scripts/format_robust_score.py qwen    # 51_.../runs/*/e2e/results.jsonl
    python scripts/format_robust_score.py ds      # runs/*_dsflash/e2e/results.jsonl
"""
import json
import re
import sys
from pathlib import Path
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repro import common as C  # noqa: E402
from repro import evaluate, scoring  # noqa: E402

# point EVID_QWEN_ARCHIVE at a local copy of the internal 51 archive
QWEN_ROOT = Path(os.environ.get("EVID_QWEN_ARCHIVE", "."))
NUM = re.compile(r"-?\$?\d[\d,]*\.?\d*\s*%?")
NEG_CUES = ("decrease", "decline", "drop", "lower", "loss", "down", "reduction",
            "fell", "decreased", "declined", "negative")


def leading_value(answer):
    """Return the value the answer leads with; used only by the sensitivity check.

    Two repairs, tried in order, and only after the frozen rule has failed:
      1. a leading yes/no/true/false token;
      2. the first number, with the sign taken from a preceding
         "decrease/decline/drop/lower/loss" cue.
    Unit words are dropped because ``E.num`` ignores them anyway.  String
    answers keep their frozen verdict, so this can only turn a miss into a hit.
    """
    text = str(answer or "").strip()
    if not text:
        return [text]
    candidates = []
    head = re.match(r"^(yes|no|true|false)\b", text, re.I)
    if head:
        candidates.append(head.group(1))
    hit = NUM.search(text)
    if hit:
        value = hit.group(0).replace(" ", "")
        prefix = text[:hit.start()].lower()
        if any(cue in prefix for cue in NEG_CUES) and not value.startswith("-"):
            value = "-" + value
        candidates.append(value)
    return candidates


def matches(sid, answer, gold):
    return evaluate.label(sid, answer, gold)


def relaxed_label(sid, answer, gold):
    """Frozen verdict, then the repairs above; never downgrades a frozen hit."""
    frozen = evaluate.label(sid, answer, gold)
    if frozen == "C":
        return "C"
    for candidate in leading_value(answer):
        if candidate and evaluate.label(sid, candidate, gold) == "C":
            return "C"
    return frozen


def score_file(results_path, eval_path):
    records = [json.loads(line) for line in open(results_path, encoding="utf-8")]
    gold = {r["sample_id"]: r for r in C.rows(eval_path)}
    runtime = {r["sample_id"]: r for r in C.rows(Path(eval_path).parent / "runtime.jsonl")}
    out, labels = {}, {}
    for arm in ("SSC", "EVID"):
        present = [r for r in records if arm in r["arms"]]
        frozen_labels = {r["sample_id"]: matches(r["sample_id"], r["arms"][arm].get("answer"), gold)
                         for r in present}
        relaxed_labels = {r["sample_id"]: relaxed_label(r["sample_id"], r["arms"][arm].get("answer"), gold)
                          for r in present}
        labels[arm] = {"frozen": frozen_labels, "relaxed": relaxed_labels}
        out[arm] = {"n": len(present),
                    "frozen": sum(v == "C" for v in frozen_labels.values()),
                    "relaxed": sum(v == "C" for v in relaxed_labels.values())}
    for reading in ("frozen", "relaxed"):
        out["compare_" + reading] = evaluate.paired(
            labels["SSC"][reading], labels["EVID"][reading],
            {sid: runtime[sid] for sid in labels["SSC"][reading]})
        out["compare_" + reading]["per_source"] = {
            source: {"n": sum(1 for sid in labels["SSC"][reading]
                              if runtime[sid]["dataset"] == source),
                     "SSC": sum(1 for sid, v in labels["SSC"][reading].items()
                                if runtime[sid]["dataset"] == source and v == "C"),
                     "EVID": sum(1 for sid, v in labels["EVID"][reading].items()
                                 if runtime[sid]["dataset"] == source and v == "C")}
            for source in sorted({r["dataset"] for r in runtime.values()})}
    return out


def main(argv):
    which = (argv[0] if argv else "ds").lower()
    rows = []
    for dataset in ("mixed400", "numeric400"):
        if which.startswith("q"):
            results = QWEN_ROOT / "runs" / dataset / "e2e" / "results.jsonl"
            eval_path = QWEN_ROOT / "datasets" / dataset / "eval.jsonl"
            model = "qwen3.8-flash"
        else:
            results = Path("runs") / (dataset + "_dsflash") / "e2e" / "results.jsonl"
            eval_path = C.dataset_path(dataset + "_dsflash") / "eval.jsonl"
            model = "deepseek-v4.1-flash-1017"
        stats = score_file(results, eval_path)
        rows.append((model, dataset, stats))

    print("%-26s %-11s %6s %6s %6s %6s %10s %12s" % ("模型", "题集", "SSC原", "SSC归", "EVID原", "EVID归", "净差原", "净差归并"))
    report = {}
    for model, dataset, stats in rows:
        s, e = stats["SSC"], stats["EVID"]
        net_frozen = e["frozen"] - s["frozen"]
        net_relaxed = e["relaxed"] - s["relaxed"]
        cf, cr = stats["compare_frozen"], stats["compare_relaxed"]
        print("%-26s %-11s %6d %6d %6d %6d %+10d %+12d   p原=%.4f p归=%.4f"
              % (model, dataset, s["frozen"], s["relaxed"], e["frozen"], e["relaxed"],
                 net_frozen, net_relaxed, cf["mcnemar_raw"], cr["mcnemar_raw"]))
        print("      归并后按来源: " + "  ".join(
            "%s SSC %d/%d EVID %d/%d" % (k, v["SSC"], v["n"], v["EVID"], v["n"])
            for k, v in cr["per_source"].items()))
        report[model + "/" + dataset] = {"SSC": s, "EVID": e, "net_frozen": net_frozen,
                                         "net_relaxed": net_relaxed,
                                         "compare_frozen": cf, "compare_relaxed": cr}
    path = Path(C.ROOT) / "reports" / ("format_robust_%s.json" % ("qwen" if which.startswith("q") else "ds"))
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("写出", path)
    return report


if __name__ == "__main__":
    main(sys.argv[1:])
