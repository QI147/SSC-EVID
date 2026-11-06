# `evid36` — the method package

Three files, no dependencies outside the standard library, no I/O. This is the
version that produced the paper’s numbers; the MD5 values below let you check that
they were not quietly edited while being packaged.

| File | MD5 | Lines | Role |
|---|---|---:|---|
| `evid_integrated.py` | `59b60f1757927622ef5527315607e611` | 154 | support check, gap→action, versioning, reassessment, final gate |
| `fact_first.py` | `0e9e98a016800e434a9cd4e1d6f28076` | 138 | observation-first initial answer + explicit calculator |
| `fact_protocol.py` | `3bd68c8afe3a1d4d8f3f93b5da192f2c` | 148 | compact observation format, strict JSON, loss-aware recovery |

These hashes are for the internal source files, which use CRLF line endings. Git
stores the same content with LF, so `md5sum` on a clone returns different values;
compare with `tr -d '\r'` or `git show 38418da:evid36/<file>` instead. The content
is identical to the internal `methods/evid36`.

The checked-out tree also carries `variant.py` and optional prompt-contract
switches (37/38/39 here, 40/41 under `experiments/zh_opt16/variants/`), selected
once at import by `EVID_PROMPT_VARIANT`. The default `36` is the published
contract; the switches are inert unless the variable is set. With the default,
`python -m unittest discover -s tests` and
`python scripts/verify_reported_numbers.py` both pass unchanged.

## Using it without the harness

```python
from evid36 import evid_integrated as E36
from evid36 import fact_first as F36

def invoke(arm, phase, system, prompt, image):
    """Return {"status": "ok", "finish_reason": "stop", "content": "<model text>"}."""
    ...

item = {"sample_id": "q1", "question": "...", "image": "table.png"}
initial, record = F36.initial(item, invoke)      # may be None if no defensible answer
result = E36.run(item, initial, invoke) if initial else {"answer": None}
print(result["status"], result["answer"])
```

`invoke` receives the arm (`EVID_FACTS`, `EVID_INIT`, `EVID`), the phase
(`observe_facts`, `initial_answer`, `evidence_check`, `acquire_*`,
`evidence_reassess_and_correct`, `recheck`), an always-empty system string, the full
prompt, and the image path. It must not raise on a model error: return
`status="error"` instead, because the loop turns that into a documented fallback.

The image argument is passed through untouched, so any loader works (file path, PIL
image, tensor). This package never opens it.

## What each module guarantees

`fact_protocol.observation`
: accepts exactly one JSON object; on truncation keeps only complete records and
  marks the range `OPEN`; never infers a missing row; marks empty cells as
  `[EXPLICIT_EMPTY_CELL]`; renames duplicate ids and downgrades them to `unverified`;
  records every defect in `issues` instead of defaulting to a clean state.

`fact_first`
: builds the initial answer only from cited observations; the calculator rejects
  non-numeric or non-image operands and labels every result
  `LISTED_OPERANDS_ONLY`; a missing citation is flagged but does not discard an
  otherwise defensible answer.

`evid_integrated`
: the seven gap types and their directives, the action derived from `gap.type`, the
  reference/schema checks, the version append, the “changed answer requires
  incorrect” rule, and the final gate. Any violation returns
  `status="FALLBACK_UNVERIFIED"` with the reason in `unresolved`.

## Reading a trace

```python
{"status": "CORRECTED_EVIDENCE_SUPPORTED",
 "answer": "1500",
 "candidates": [{"source": "initial", "answer": "1200"},
                {"source": "evidence_correction", "answer": "1500", "evidence_version": 1}],
 "evidence_versions": [{"version": 0, "evidence": [...]},
                       {"version": 1, "evidence": [...], "scope": "...", "unresolved": []}],
 "events": [{"phase": "action_selected", "gap": {...}, "action": "A_BIND", "basis_version": 0},
            {"phase": "checks_invalidated", "checks": ["answer_support", "scope_support"], ...}],
 "check": {...}, "repair": {...}, "final_check": {...}, "calls": [...]}
```

`calls` holds the raw transport metadata (call id, usage, finish reason, cost,
whether it was reused), so a change in the answer can be traced to the exact request
that produced the new observation.

## Tests

`tests/test_evid36_flow.py` runs the whole loop against scripted responses; it covers
keep, correct, gate-blocked, unresolved-`correct`, unknown-reference and missing-initial
paths. `tests/test_protocol.py` covers the parser’s loss handling.
