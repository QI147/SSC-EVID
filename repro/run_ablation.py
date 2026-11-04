"""Five-arm module ablation: run the full loop and four single-module deletions.

Each item is answered by all five arms from the same image and question. Arms
share one fingerprint cache, so an identical prefix (same prompt, same image) is
reused across arms instead of being re-sampled — the difference between two arms
therefore comes from the deleted module, not from a fresh draw of the prefix.

Usage::

    python -m repro.run_ablation --dataset mixed400 --shard 0/8 --authorize-paid
    python -m repro.run_ablation --dataset mixed400 --command merge --shards 8
"""
import argparse
import collections
import json
import sys
from pathlib import Path

from . import ablation
from . import common as C
from . import runner as X

MODE = "ablation"
LIMITS = {arm: 6 for arm in ablation.ARMS}


def run(dataset, shard):
    frozen = X.verify(dataset)
    runtime = C.rows(C.dataset_path(dataset) / "runtime.jsonl")
    out_dir = X.folder(dataset, MODE)
    out_dir.mkdir(parents=True, exist_ok=True)
    if (out_dir / "quarantined.json").exists():
        raise ValueError("quarantined run")
    index, shards = map(int, shard.split("/"))
    path = out_dir / ("results_s%d.jsonl" % index)
    with (out_dir / ("run_s%d.lock" % index)).open("a+b") as lock:
        X.locked(lock)
        try:
            output = C.rows(path) if path.exists() else []
            done = {r["sample_id"] for r in output}
            if len(output) != len(done) or any(r["freeze_sha256"] != frozen["sha256"] for r in output):
                raise ValueError("mixed or duplicate result")
            transport = X.Invoke(dataset, MODE, "_s%d" % index, frozen, limits=LIMITS)
            for position, item in enumerate(runtime):
                if position % shards != index or item["sample_id"] in done:
                    continue
                invoke = transport.for_item(item)
                record = {"sample_id": item["sample_id"], "dataset": item["dataset"], "arms": {}}
                for arm in ablation.ARMS:
                    initial, initial_record, outcome = ablation.run_arm(arm, item, invoke)
                    record["arms"][arm] = {"answer": outcome.get("answer"),
                                           "status": outcome.get("status"),
                                           "calls": len(outcome.get("calls", [])),
                                           "initial": initial,
                                           "initial_calls": initial_record,
                                           "trace": outcome}
                record["freeze_sha256"] = frozen["sha256"]
                output.append(record)
                X.save_rows(path, output)
                print(json.dumps({"dataset": dataset, "shard": shard,
                                  "completed": len(output), "last": item["sample_id"]}), flush=True)
        finally:
            X.unlock(lock)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Five-arm module ablation.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--command", choices=["run", "merge"], default="run")
    parser.add_argument("--shard", default="0/1")
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--authorize-paid", action="store_true")
    args = parser.parse_args(argv)
    X._apply_model_config()
    if args.command == "merge":
        X.merge(args.dataset, MODE, args.shards)
        return
    if not args.authorize_paid:
        parser.error("explicit --authorize-paid required")
    run(args.dataset, args.shard)


if __name__ == "__main__":
    main()
