"""Derive each question's independent-vote budget from the EVID end-to-end ledger.

Budget = prompt + completion tokens of every EVID call for that question in the
end-to-end run (image tokens included, shared initial excluded). The number is
written to ``datasets/<set>/pure_budget.json`` and enters the freeze, so a later
change is detected rather than silently rebudgeting the control.
"""
import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repro import common as C  # noqa: E402
from repro import runner as X  # noqa: E402

EVID_ARMS = ("EVID_FACTS", "EVID_INIT", "EVID")


def build(dataset):
    ledger = [r for p in X.folder(dataset, "e2e").glob("*_calls.jsonl")
              for r in C.rows(p) if not r.get("reused") and r.get("arm") in EVID_ARMS]
    if not ledger:
        raise SystemExit("no EVID calls in runs/%s/e2e: run the e2e stage first" % dataset)
    budget = collections.Counter()
    for record in ledger:
        usage = record.get("usage") or {}
        if "prompt_tokens" not in usage or "completion_tokens" not in usage:
            raise SystemExit("call %s has no usage; the budget cannot be derived" % record.get("call_id"))
        budget[record["sid"]] += usage["prompt_tokens"] + usage["completion_tokens"]
    runtime = C.rows(C.dataset_path(dataset) / "runtime.jsonl")
    expected = {r["sample_id"] for r in runtime}
    if set(budget) != expected:
        raise SystemExit("budget covers %d ids but the cohort has %d" % (len(budget), len(expected)))
    path = C.dataset_path(dataset) / "pure_budget.json"
    path.write_text(json.dumps(dict(budget), ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"dataset": dataset, "n": len(budget), "total": sum(budget.values()),
                      "path": str(path)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    build(parser.parse_args().dataset)
