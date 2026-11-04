"""Copy table images into the repository tree and verify every SHA256.

Images are not redistributed with the code, so each user supplies their own copy
(downloaded benchmark, or the private archive). This script copies only the files
named by ``runtime.jsonl`` and refuses to continue on a hash mismatch, which keeps
"the image used in the paper" and "the image on disk" identical.

    python scripts/import_images.py --dataset mixed400 --source D:/data/mixed400/images
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repro import common as C  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--source", required=True, help="folder holding the images by file name")
    parser.add_argument("--move", action="store_true", help="move instead of copy")
    args = parser.parse_args(argv)
    source = Path(args.source)
    if not source.is_dir():
        raise SystemExit("not a folder: %s" % source)
    runtime = C.rows(C.dataset_path(args.dataset) / "runtime.jsonl")
    target_dir = C.dataset_path(args.dataset) / "images"
    target_dir.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    problems = []
    for record in runtime:
        name = Path(record["image"]).name
        candidate = source / name
        if not candidate.exists():
            problems.append("missing %s" % name)
            continue
        digest = C.sha256_file(candidate)
        if digest != record["image_sha256"]:
            problems.append("hash mismatch %s" % name)
            continue
        destination = target_dir / name
        if destination.exists() and C.sha256_file(destination) == digest:
            skipped += 1
            continue
        (shutil.move if args.move else shutil.copyfile)(str(candidate), str(destination))
        copied += 1
    print("dataset=%s copied=%d already_present=%d problems=%d"
          % (args.dataset, copied, skipped, len(problems)))
    for problem in problems[:20]:
        print("  " + problem)
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
