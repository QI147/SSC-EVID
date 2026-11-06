"""Build frozen local subsets of TableVQA-Bench (vwtq_syn / vtabfact).

Output layout matches the harness contract used by ``repro/common.py``::

    datasets/<name>/runtime.jsonl   question + image path + hashes (safe to send)
    datasets/<name>/eval.jsonl      gold answers (scoring only)
    datasets/<name>/manifest.json   counts + frozen scope note
    datasets/<name>/images/*.jpg

Design notes
------------
* VWTQ-Syn has 150 images but 250 questions, so the same image repeats. Images
  are de-duplicated by content hash: one file per unique image, several rows may
  point at it. ``source_group`` is therefore the table-html hash, not the image.
* ``source_group`` uses a normalised hash of the table HTML so table overlap with
  ``datasets/mixed400`` can be measured before any run (see check_overlap()).
* Nothing here reads model outputs. Run this once, commit the manifest hash, and
  do not regenerate after seeing results.

Usage::

    python scripts/build_tablevqa_subsets.py
    python scripts/build_tablevqa_subsets.py --check-overlap
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "datasets"

SUBSETS = {
    "vwtq_syn": "vwtqsyn250",
    "vtabfact": "vtabfact250",
}


def norm_html(text):
    """Hash a table's HTML so that re-rendered copies of one table collide."""
    if not text:
        return None
    flat = re.sub(r"\s+", " ", str(text)).strip().lower()
    return hashlib.sha256(flat.encode("utf-8")).hexdigest()


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def pick(row, *names):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def build(split, name, force=False):
    from datasets import load_dataset

    out = DATA / name
    if (out / "runtime.jsonl").exists() and not force:
        print("[skip] %s already built (use --force to rebuild)" % name)
        return

    images_dir = out / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    dataset = load_dataset("terryoo/TableVQA-Bench", split=split)
    print("[read] %s: %d rows" % (split, len(dataset)))

    runtime, gold = [], []
    image_cache = {}          # content hash -> relative path
    group_count = {}

    for index, row in enumerate(dataset):
        image = row["image"]
        if image.mode != "RGB":
            image = image.convert("RGB")
        buffer = image.tobytes()

        # Raw pixels are the stable identity for de-duplicating rows, but the
        # frozen harness verifies the SHA256 of the *file on disk*.  JPEG
        # re-encoding changes those bytes, so the recorded digest must be taken
        # after saving, not from the pixel buffer.
        key = sha256_bytes(buffer)
        if key not in image_cache:
            filename = "%s-%s.jpg" % (name[:8], key[:12])
            path = images_dir / filename
            if not path.exists():
                image.save(path, "JPEG", quality=95)
            file_digest = sha256_bytes(path.read_bytes())
            image_cache[key] = (path.relative_to(ROOT).as_posix(), file_digest)
        image_path, file_digest = image_cache[key]

        table_hash = norm_html(pick(row, "text_html_table", "text_markdown_table"))
        group_count[table_hash] = group_count.get(table_hash, 0) + 1

        sid = "%s-%04d" % (name[:8].upper(), index)
        runtime.append({
            "sample_id": sid,
            "dataset": split,
            "question": str(row["question"]).strip(),
            "image": image_path,
            "image_sha256": file_digest,
            "source": {
                "pool": "TableVQA-Bench",
                "shard": split,
                "qa_id": str(pick(row, "qa_id", "id") or index),
            },
            "source_group": table_hash,
            "source_group_basis": "normalized_table_html_sha256",
            "table_pool_size": group_count[table_hash],
            "selected_from_same_table": group_count[table_hash],
            "order": index,
            "cohort": name,
            "input_condition": "native_table_image",
            "experiment_family": split,
        })
        gold.append({
            "sample_id": sid,
            "answer": str(row["gt"]).strip(),
            "exe_ans": None,
        })

    # Backfill the final pool size now that every table has been seen.
    for record in runtime:
        record["table_pool_size"] = group_count[record["source_group"]]
        record["selected_from_same_table"] = group_count[record["source_group"]]

    with open(out / "runtime.jsonl", "w", encoding="utf-8") as handle:
        for record in runtime:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    with open(out / "eval.jsonl", "w", encoding="utf-8") as handle:
        for record in gold:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    answers = [g["answer"].lower() for g in gold]
    manifest = {
        "n": len(runtime),
        "unique_images": len(image_cache),
        "unique_tables": len(group_count),
        "answer_distribution": {a: answers.count(a) for a in sorted(set(answers))},
        "source_counts": {split: len(runtime)},
        "runtime_sha256": sha256_bytes((out / "runtime.jsonl").read_bytes()),
        "eval_sha256": sha256_bytes((out / "eval.jsonl").read_bytes()),
        "scope": "public benchmark subset; frozen before any model run; "
                 "not used for prompt or branch selection",
        "built_from": "huggingface:terryoo/TableVQA-Bench:%s" % split,
    }
    with open(out / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    print("[done] %s: %d questions / %d images / %d tables"
          % (name, len(runtime), len(image_cache), len(group_count)))
    print("       answers: %s" % manifest["answer_distribution"])


def load_groups(path):
    groups = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                if record.get("source_group"):
                    groups.add(record["source_group"])
    return groups


def check_overlap(reference="mixed400", targets=("vwtqsyn250", "vtabfact250")):
    """Report table overlap. This decides how the paper may phrase 'held-out'."""
    ref_path = DATA / reference / "runtime.jsonl"
    if not ref_path.exists():
        print("[warn] %s not found; cannot measure overlap" % ref_path)
        return
    ref = load_groups(ref_path)
    print("reference %s: %d tables" % (reference, len(ref)))
    for name in targets:
        path = DATA / name / "runtime.jsonl"
        if not path.exists():
            print("  %-12s not built" % name)
            continue
        cur = load_groups(path)
        shared = ref & cur
        pct = 100.0 * len(shared) / max(len(cur), 1)
        print("  %-12s %3d tables, %3d shared with %s (%.1f%%)"
              % (name, len(cur), len(shared), reference, pct))
        if pct >= 30:
            print("      -> phrase as 'unseen renderings of the same table pool', "
                  "NOT as a held-out domain")
        else:
            print("      -> may be phrased as a new table source")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-overlap", action="store_true")
    parser.add_argument("--only", choices=sorted(SUBSETS), default=None)
    args = parser.parse_args()

    if args.check_overlap:
        check_overlap()
        return

    for split, name in SUBSETS.items():
        if args.only and args.only != split:
            continue
        build(split, name, force=args.force)

    check_overlap()


if __name__ == "__main__":
    main()
