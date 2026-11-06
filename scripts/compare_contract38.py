"""Contract 36 vs 38 on the same items: Chinese held-out + English main sets.

This closes the missing control for the contract optimisation:
  * 38 was tuned on the English 400-question sets with deepseek-v4.1-flash-1017;
  * here it is run on qwen3.8-flash (the paper's backbone) and on the Chinese
    held-out set, which has never been used for any design decision.

Reports, per dataset:
  1. SSC / EVID scores under each contract, with paired McNemar;
  2. EVID 36 -> 38 paired change on identical items;
  3. SSC cross-batch agreement (a drift check: the SSC prompt is unchanged).

Usage:
    python scripts/compare_contract38.py
"""
import json
import sys
from pathlib import Path
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repro import common as C, evaluate  # noqa: E402
from zh_robust_score import relaxed_label as zh_relaxed  # noqa: E402

# point EVID_QWEN_ARCHIVE at a local copy of the internal 51 archive
QWEN = Path(os.environ.get("EVID_QWEN_ARCHIVE", "."))

CASES = [
    ("中文 200", "zh_tableeval_qwen", "zh_tableeval_qwen38", "zh"),
    ("混合 400", "@qwen/mixed400", "mixed400_qwen38", "en"),
    ("数字 400", "@qwen/numeric400", "numeric400_qwen38", "en"),
]


def paths(spec):
    """Return (results.jsonl, eval.jsonl, runtime.jsonl) for a spec."""
    if spec.startswith("@qwen/"):
        name = spec.split("/", 1)[1]
        return (QWEN / "runs" / name / "e2e" / "results.jsonl",
                QWEN / "datasets" / name / "eval.jsonl",
                QWEN / "datasets" / name / "runtime.jsonl")
    return (C.folder(spec, "e2e") / "results.jsonl",
            C.dataset_path(spec) / "eval.jsonl",
            C.dataset_path(spec) / "runtime.jsonl")


def load(spec):
    rp, ep, tp = paths(spec)
    if not rp.exists():
        return None
    records = [json.loads(x) for x in rp.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {
        "recs": {r["sample_id"]: r for r in records},
        "gold": {r["sample_id"]: r for r in C.rows(ep)},
        "runtime": {r["sample_id"]: r for r in C.rows(tp)},
    }


def labels(data, arm, kind):
    fn = (lambda sid, a: zh_relaxed(sid, a, data["gold"])) if kind == "zh_relaxed" \
        else (lambda sid, a: evaluate.label(sid, a, data["gold"]))
    return {sid: fn(sid, rec["arms"][arm].get("answer"))
            for sid, rec in data["recs"].items() if arm in rec["arms"]}


def paired(a, b, runtime):
    ids = [s for s in a if s in b]
    return evaluate.paired({s: a[s] for s in ids}, {s: b[s] for s in ids},
                           {s: runtime[s] for s in ids}), len(ids)


def main():
    print("契约 36 vs 38（同题同图，SSC 提示未变）\n")
    all_rows = []
    for title, old_spec, new_spec, lang in CASES:
        old, new = load(old_spec), load(new_spec)
        print("=" * 78)
        print(title)
        if old is None or new is None:
            print("  结果未就绪：36=%s 38=%s" % (old is not None, new is not None))
            continue
        common = [s for s in old["recs"] if s in new["recs"]]
        print("  共同题目 %d（36: %d，38: %d）" %
              (len(common), len(old["recs"]), len(new["recs"])))

        for kind in (["frozen", "zh_relaxed"] if lang == "zh" else ["frozen"]):
            tag = "主评分" if kind == "frozen" else "中文宽松 v2"
            print("  --- %s ---" % tag)
            for name, data in (("36", old), ("38", new)):
                ssc, evid = labels(data, "SSC", kind), labels(data, "EVID", kind)
                p, n = paired(ssc, evid, data["runtime"])
                print("    %s 版  SSC %3d  EVID %3d  净 %+3d  p=%.4f  (n=%d)" %
                      (name, sum(v == "C" for v in ssc.values()),
                       sum(v == "C" for v in evid.values()),
                       p["net"], p["mcnemar_raw"], n))
                all_rows.append((title, tag, name, sum(v == "C" for v in evid.values())))

            a_all, b_all = labels(old, "EVID", kind), labels(new, "EVID", kind)
            a = {s: a_all[s] for s in common}
            b = {s: b_all[s] for s in common}
            p, n = paired(a, b, old["runtime"])
            print("    EVID 36→38: 净 %+d（得 %d / 失 %d）p=%.4f" %
                  (p["net"], p["gained"], p["lost"], p["mcnemar_raw"]))

        s36, s38 = labels(old, "SSC", "frozen"), labels(new, "SSC", "frozen")
        ids = [s for s in common if s in s36 and s in s38]
        flips = sum(s36[s] != s38[s] for s in ids)
        print("  SSC 跨批次：%d/%d 题判分翻转（%.1f%%）——SSC 提示未变，这是漂移量级" %
              (flips, len(ids), 100 * flips / max(len(ids), 1)))
    print("\n完成。中文集是 38 版的 held-out；本脚本只读结果，不改任何协议。")


if __name__ == "__main__":
    main()
