"""Compare the 36 and 37 prompt contracts on the same backbone and questions.

Reads three namespaces per dataset:
  * ``<ds>_dsflash``    SSC + EVID under the published contract (36)
  * ``<ds>_dsflash37``  EVID only under the bare-value contract (37)

and prints frozen / value-first / entity readings, contract compliance
(answer length, share of answers containing a parenthesis) and evidence volume.

Usage: python scripts/compare_variants.py
"""
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repro import common as C  # noqa: E402
from repro import evaluate  # noqa: E402
from format_robust_score import relaxed_label  # noqa: E402


def rows(namespace):
    path = Path("runs") / namespace / "e2e" / "results.jsonl"
    if not path.exists():
        return None
    return {r["sample_id"]: r for r in (json.loads(line) for line in open(path, encoding="utf-8"))}


def gold_of(namespace):
    return {r["sample_id"]: r for r in C.rows(C.dataset_path(namespace) / "eval.jsonl")}


def source_of(namespace):
    return {r["sample_id"]: r["dataset"] for r in C.rows(C.dataset_path(namespace) / "runtime.jsonl")}


def entity_reading(sid, answer, gold):
    """Upper bound: the gold entity appears as a token run inside the answer."""
    g = str(gold[sid].get("answer") or "")
    if not re.search(r"[A-Za-z]", g):
        return False
    norm = lambda s: re.sub(r"[^a-z0-9.%\- ]", "", str(s).lower()).strip()
    ng, na = norm(g), norm(answer)
    return bool(ng) and (" " + ng + " ") in (" " + na + " ")


def measure(record, arm, gold):
    ans = record["arms"][arm].get("answer")
    return {
        "frozen": evaluate.label(record["sample_id"], ans, gold) == "C",
        "relaxed": relaxed_label(record["sample_id"], ans, gold) == "C",
        "entity": entity_reading(record["sample_id"], ans, gold),
        "len": len(str(ans or "")),
        "paren": "(" in str(ans or ""),
    }


def summarise(name, records, arm, gold, sources):
    rows_ = [measure(r, arm, gold) for r in records.values() if arm in r["arms"]]
    if not rows_:
        return None
    out = {"n": len(rows_)}
    for key in ("frozen", "relaxed", "entity"):
        out[key] = sum(r[key] for r in rows_)
    out["len_mean"] = round(statistics.mean(r["len"] for r in rows_), 1)
    out["paren_share"] = round(100 * sum(r["paren"] for r in rows_) / len(rows_))
    out["by_source"] = {}
    for source in sorted(set(sources.values())):
        ids = [sid for sid in records if sources[sid] == source and arm in records[sid]["arms"]]
        out["by_source"][source] = {
            "n": len(ids),
            "frozen": sum(evaluate.label(s, records[s]["arms"][arm].get("answer"), gold) == "C" for s in ids),
            "relaxed": sum(relaxed_label(s, records[s]["arms"][arm].get("answer"), gold) == "C" for s in ids),
        }
    return out


def main():
    for dataset in ("mixed400", "numeric400"):
        base = rows(dataset + "_dsflash")
        if base is None:
            print("%s: 缺少 36 版结果" % dataset)
            continue
        gold = gold_of(dataset + "_dsflash")
        sources = source_of(dataset + "_dsflash")
        table = [("SSC", summarise("SSC", base, "SSC", gold, sources)),
                 ("EVID-36", summarise("EVID-36", base, "EVID", gold, sources))]
        for variant in ("37", "38", "39"):
            extra = rows("%s_dsflash%s" % (dataset, variant))
            if extra is not None:
                table.append(("EVID-" + variant, summarise(variant, extra, "EVID", gold, sources)))
        print("\n%s  n=%d" % (dataset, table[0][1]["n"]))
        print("  %-8s %6s %6s %6s %7s %8s" % ("臂", "原判", "首值", "实体", "均长", "含括号"))
        for name, s in table:
            print("  %-8s %6d %6d %6d %7.1f %7d%%"
                  % (name, s["frozen"], s["relaxed"], s["entity"], s["len_mean"], s["paren_share"]))
        print("  净差（对 SSC）:")
        for name, s in table[1:]:
            print("    %-8s 原判 %+d   首值 %+d   实体 %+d"
                  % (name, s["frozen"] - table[0][1]["frozen"],
                     s["relaxed"] - table[0][1]["relaxed"],
                     s["entity"] - table[0][1]["entity"]))
        print("  分来源（原判/首值）:")
        for name, s in table:
            print("    %-8s" % name,
                  "  ".join("%s %d/%d→%d" % (k, v["frozen"], v["n"], v["relaxed"])
                            for k, v in s["by_source"].items()))


if __name__ == "__main__":
    main()
