# Method

This document describes what the code does, in the order the code does it. Every
statement below is implemented in `evid36/`; the test that pins it is named where
one exists.

## 1. State

A trial maintains

```
S_t = (a_t, E_t, C_t, U_t)
```

`a_t` is the current candidate answer, `E_t` the evidence ledger, `C_t` the check
records and `U_t` the open obligations. An acquisition appends to the ledger and
creates a new version; it never rewrites an earlier record. `run()` returns the
whole trace (`calls`, `check`, `repair`, `final_check`, `evidence_versions`,
`events`, `candidates`, `unresolved`, `status`), so a reviewer can replay why an
answer changed.

## 2. Evidence record

```python
{"id": "i3", "location": "row=4; column=2011", "content": "1,200",
 "source": "image", "provenance": {...}, "row_key": "4", "value": 1200}
```

* `id` is unique within a ledger. Initial observations use `i*`, check observations
  `c*`, acquisitions `a*`, computations `it*`. A duplicate id found in one
  observation response is renamed (`i_recovered_*`) and downgraded to
  `unverified` (`tests/test_protocol.py`).
* `location` is written by the program for compact row tuples
  (`row=<physical row>; column=<header>`), so the model does not restate it per cell.
* `source` is `image`, `calculation` or `unverified`. A record without a declared
  source becomes `unverified` and is listed in the observation issues.
* `value` is optional and only accepted when it is a pure numeric string; the
  calculator refuses anything else (`fact_first.number`).
* `row_key` and `entity` are what make counting and distinct counting auditable:
  `count` requires distinct physical rows, `count_distinct` requires explicit
  entities.

Nothing in the ledger is a truth label. It is a record of what was observed and
where, plus what the model claimed about the source.

## 3. Observation protocol (`fact_protocol`, `fact_first.initial`)

The model returns one compact object:

```json
{"question_plan": {"answer_type": "...", "condition": "...", "selection_unit": "..."},
 "scope": "rows inspected and limits", "unresolved": [],
 "rows": [["physical row", "column/header", "raw text", value?, entity?]],
 "operations": [{"id": "it1", "op": "sum", "evidence_ids": ["i1", "i2"]}]}
```

Program-side normalisation, in order:

1. `single_object` accepts exactly one JSON object; `finish_reason=length` is
   treated as no object at all.
2. If that fails, `prefix_object` walks the real top-level object and keeps only
   *complete* row records. Anything cut mid-record is dropped, `scope` is prefixed
   with `OPEN: incomplete response`, and `range_status` becomes `OPEN`. Missing rows
   are never inferred.
3. Empty strings become `[EXPLICIT_EMPTY_CELL]` with a flag: a blank is not a zero.
4. Duplicate ids are renamed and downgraded; malformed records become
   `binding_unresolved` entries.
5. Missing `question_plan` / `scope` / `unresolved` are recorded as issues rather
   than silently defaulted to a clean state.

Then, at most two arithmetic operations over the whole listed subset are evaluated.
Operands must be `source=image` with an explicit numeric value; the result carries
`range_status: LISTED_OPERANDS_ONLY`. The calculator is auxiliary: the answer call is
explicitly told that a computed value is conditional on its operands, not truth.

The initial answer call sees the ledger, the plan, the auxiliary results, the scope
and the open items, and must cite ids. Cited ids that are not in the ledger are
dropped and flagged `initial_support_not_verified`; the answer itself is kept,
because a missing citation is a contract defect, not evidence that the answer is
semantically wrong.

## 4. Support check (`evid_integrated.CHECK`)

The check re-reads the image and judges the candidate against the ledger along seven
relations:

| Relation | Question |
|---|---|
| VALUE | are the characters, sign and magnitude read correctly? |
| BINDING | does the fact belong to the asked entity, physical row and header? |
| TIME | are the year, order and boundary as required? |
| UNIT | is the scale/currency supported by a header or footnote? |
| COVERAGE | does the scan cover the required set, including exclusions? |
| CALCULATION | are the operands, operation and counting unit right? |
| QUESTION | does the returned object match the wording (list vs count, negation)? |

The output contract is enforced structurally (`judgment_issue`):

* `verdict ∈ {correct, incorrect, unknown}`, non-empty `analysis`;
* `supporting_ids` is a non-empty list of ids that exist in the ledger;
* `scope` non-empty, `unresolved` a list;
* `correct` may not carry open items;
* `correct` may not be supported only by `unverified` records.

An `incorrect`/`unknown` verdict must also carry
`gap={"type", "target", "why"}`. The action is derived from `gap.type`, not from the
model's `action` field; a mismatch is recorded as `action_normalized`.

## 5. Directed acquisition

| gap.type | action | what is requested |
|---|---|---|
| VALUE | `A_VALUE` | reread the specified cell and its labels, keep raw characters |
| BINDING | `A_BIND` | trace the value to its physical row and full header |
| TIME | `A_TIME` | read the date/interval/status and the inclusive boundaries |
| UNIT | `A_UNIT` | read header/footnote, give an explicit conversion with operands |
| COVERAGE | `A_SCOPE` | inspect the required range, record qualifying and excluded items |
| CALCULATION | `A_CALC` | fetch the raw operands, redo the arithmetic explicitly |
| QUESTION | `A_INTERPRET` | compare wording with headers, keep unresolved alternatives |

The acquisition prompt contains the original question, the action and the gap
target, and is told not to output an answer or a verdict. Its response is evidence
plus a scope statement; a missing scope is a hard failure (`acquisition_scope_missing`),
because an acquisition without a stated range cannot bound its own claim.

## 6. Versioning and reassessment

New evidence triggers `checks_invalidated` for `answer_support` and `scope_support`
and appends `evidence_versions[1]` with the acquisition's scope and open items. The
reassessment call receives the full ledger, the diagnosis and the acquisition limits.
It may keep the answer or propose a new one, and:

* a changed answer requires `verdict=incorrect` (`changed_without_incorrect`);
* `unknown`, or any open item, ends the trial rather than delivering a guess;
* new observations are not allowed in this call — anything newly noticed must be
  disclosed as unresolved, because only an acquisition may extend the ledger.

## 7. Final gate

The gate is a separate call with `(question, candidate, reasoning, ledger,
acquisition limits)`. It does not see the previous correctness conclusion. Delivery
requires `verdict=correct`, valid references and an empty `unresolved`; otherwise the
initial candidate is delivered with `status=FALLBACK_UNVERIFIED` and the reason is
kept in `unresolved` (for example `final_not_supported`).

The gate is conservative by construction: in the paper's case analysis it sometimes
blocks a correct candidate whose own analysis text contradicted its verdict field.
That behaviour is reported, not hidden — the program follows the field, and the
trace keeps both.

## 8. Statuses

| Status | Meaning |
|---|---|
| `KEPT_EVIDENCE_SUPPORTED` | check said correct with complete support; no acquisition |
| `CORRECTED_EVIDENCE_SUPPORTED` | acquisition → new candidate → gate accepted, answer changed |
| `KEPT_AFTER_ACQUISITION` | same path, answer unchanged |
| `FALLBACK_UNVERIFIED` | anything unproven: unparseable check, invalid reference, unresolved gap, gate refusal |
| `INITIAL_MISSING` | no initial answer to work on |

`FALLBACK_UNVERIFIED` always carries one or more `unresolved` reasons. The runner
records the status per item; the scorer never reads it.

## 9. Budget

| Mode | Calls per item | Composition |
|---|---|---|
| `initial` (COMMON) | 1 | answer + two-sentence reasoning |
| `e2e` EVID | ≤ 6 | observe, initial answer, check, acquisition, reassess, gate |
| `repair` EVID | ≤ 4 per round, ≤ 2 rounds | check, acquisition, reassess, gate |
| `fin250` | ≤ 6 | same as e2e, two arms |
| `pure` vote | ≤ 16 | independent reads until the token target |

Budgets are request counts, not a token pool. Early stop happens when the check says
`correct` and the answer did not change; the second round then records
`EARLY_STOP_CARRIED_FORWARD` instead of silently reusing the first round's numbers.

## 10. Responsibility split

| The model decides | The program decides |
|---|---|
| what the table says | whether the evidence record is well formed |
| which support relation is missing | which action that gap maps to |
| whether the candidate is supported | whether the citation exists and the contract holds |
| the delivered answer text | the arithmetic over explicit operands |
| | call order, quotas, versions, and the fallback |

The program never decides semantic truth. That is why a `correct` verdict with an
open obligation is rejected structurally rather than argued with.
