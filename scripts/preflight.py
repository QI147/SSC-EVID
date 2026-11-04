"""Zero-cost preflight: check manifests, images, gold coverage and the request budget.

Run this before spending anything. It never sends a request and never imports the
scorer into a model process.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repro import common as C  # noqa: E402
from repro import config  # noqa: E402
from repro import runner as X  # noqa: E402


def check_dataset(dataset, strict):
    runtime = C.rows(C.dataset_path(dataset) / "runtime.jsonl")
    gold = C.rows(C.dataset_path(dataset) / "eval.jsonl")
    problems = []
    if not runtime:
        problems.append("runtime.jsonl is empty")
    ids = [r["sample_id"] for r in runtime]
    if len(ids) != len(set(ids)):
        problems.append("duplicate sample_id")
    gold_ids = {r["sample_id"] for r in gold}
    if gold_ids and gold_ids != set(ids):
        problems.append("eval.jsonl does not cover the cohort (%d vs %d)" % (len(gold_ids), len(ids)))
    missing, drifted = [], []
    for record in runtime:
        path = C.ROOT / record["image"]
        if not path.exists():
            missing.append(record["sample_id"])
        elif C.sha256_file(path) != record["image_sha256"]:
            drifted.append(record["sample_id"])
    if missing:
        problems.append("%d images missing (see docs/data.md)" % len(missing))
    if drifted:
        problems.append("%d images do not match their recorded SHA256" % len(drifted))
    freeze_path = C.RUNS / dataset / "freeze.json"
    frozen = "absent"
    if freeze_path.exists():
        try:
            X.verify(dataset)
            frozen = "valid"
        except ValueError as exc:
            frozen = "drift: %s" % exc
    caps = {mode: len(runtime) * sum(limits.values()) for mode, limits in X.LIMITS.items()}
    print("%-9s n=%-4d gold=%-4d freeze=%s" % (dataset, len(runtime), len(gold_ids), frozen))
    print("          request caps: " + ", ".join("%s=%d" % (k, v) for k, v in caps.items()))
    if problems:
        print("          " + "\n          ".join(problems))
    if strict and problems:
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", nargs="+", default=["mixed400", "numeric400", "fin250"])
    parser.add_argument("--strict", action="store_true", help="fail when an image is missing")
    args = parser.parse_args(argv)
    ok = True
    for dataset in args.datasets:
        if not (C.dataset_path(dataset) / "runtime.jsonl").exists():
            print("%-9s no manifest" % dataset)
            ok = False
            continue
        ok = check_dataset(dataset, args.strict) and ok
    print("\nmodel=%s  base_url=%s" % (config.MODEL, config.BASE_URL))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
