"""Score every finished run and write machines-readable tables under ``reports/``.

Only runs whose ``results.jsonl`` exists are scored, so a partially executed
project still produces the tables it can support instead of failing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repro import common as C  # noqa: E402
from repro import evaluate, score_ablation, score_fin250, score_pure  # noqa: E402
from repro import runner as X  # noqa: E402


def done(dataset, mode):
    return (X.folder(dataset, mode) / "results.jsonl").exists()


def main():
    scored = []
    for dataset in ("mixed400", "numeric400"):
        if all(done(dataset, mode) for mode in ("initial", "e2e", "repair")) \
                and (C.dataset_path(dataset) / "eval.jsonl").exists():
            evaluate.evaluate(dataset)
            scored.append(dataset + ":main")
    if done("fin250", "fin250"):
        score_fin250.main([])
        scored.append("fin250")
    pure_datasets = [d for d in ("mixed400", "numeric400", "fin250") if done(d, "pure")]
    if pure_datasets:
        score_pure.main(["--datasets"] + pure_datasets)
        scored.append("pure")
    ablation_datasets = [d for d in ("mixed400", "numeric400") if done(d, "ablation")]
    if ablation_datasets:
        score_ablation.main(["--datasets"] + ablation_datasets)
        scored.append("ablation")
    print("scored:", ", ".join(scored) if scored else "nothing (no results.jsonl found)")


if __name__ == "__main__":
    main()
