"""Paths and file hashing shared by the runner and the scorers.

Layout (matching the image paths recorded inside ``runtime.jsonl``)::

    datasets/<set>/runtime.jsonl   question + image path + hashes (safe to send)
    datasets/<set>/eval.jsonl      gold answers (scoring only, never sent)
    datasets/<set>/images/...      table images (not in git, see docs/data.md)
    runs/<set>/<mode>/             frozen config, per-shard results, call ledger
    reports/<set>/                 metrics and per-sample predictions
"""
import hashlib
import json
import os
from pathlib import Path

# The repository root, or EVID_ROOT when the data/run tree lives elsewhere
# (for example on a large scratch disk).
REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("EVID_ROOT") or REPO_ROOT).resolve()

DATA = ROOT / "datasets"
RUNS = ROOT / "runs"
REPORTS = ROOT / "reports"

# Kept for call sites that mirror the internal layout.
REC_C1 = str(RUNS)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dataset_path(dataset):
    return DATA / dataset


def folder(dataset, mode):
    return RUNS / dataset / mode


def rows(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
