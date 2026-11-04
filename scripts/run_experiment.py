"""One entry point for a shard of any stage.

Examples
--------
    python scripts/run_experiment.py --dataset mixed400 --stage initial --shard 0/8
    python scripts/run_experiment.py --dataset mixed400 --stage e2e --shard 3/8
    python scripts/run_experiment.py --dataset mixed400 --stage repair --shard 0/8 --no-ssc
    python scripts/run_experiment.py --dataset mixed400 --merge --shards 8

Every ``run`` needs ``--authorize-paid``: this harness spends real requests and
refuses to do so implicitly.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repro import runner as X  # noqa: E402
from repro import run_ablation  # noqa: E402

STAGES = ("initial", "e2e", "repair", "fin250", "pure", "ablation")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run or merge one stage of the SSC-EVID harness.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--stage", choices=STAGES, default="e2e")
    parser.add_argument("--shard", default="0/1")
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--no-ssc", action="store_true")
    parser.add_argument("--authorize-paid", action="store_true")
    args = parser.parse_args(argv)
    X._apply_model_config()
    if args.merge:
        if args.stage == "ablation":
            X.merge(args.dataset, "ablation", args.shards)
        else:
            X.merge(args.dataset, args.stage, args.shards)
        return
    if not args.authorize_paid:
        parser.error("explicit --authorize-paid required")
    if args.stage == "ablation":
        run_ablation.run(args.dataset, args.shard)
    else:
        X.run(args.dataset, args.stage, args.shard, with_ssc=not args.no_ssc)


if __name__ == "__main__":
    main()
