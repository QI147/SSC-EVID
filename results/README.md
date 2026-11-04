# Frozen results

These files are the per-question outputs behind the paper’s tables. They are what
`scripts/verify_reported_numbers.py` checks; the paper is only as good as this
directory.

| File | Rows | Contents |
|---|---:|---|
| `mixed400/end_to_end.csv` | 1,600 | one row per item × arm (DIRECT, REREAD2, SSC, EVID): initial answer, final answer, label, status |
| `mixed400/repair_rounds.csv` | 2,400 | item × arm × round for SSC, EVID and RR_MATCH (KEEP is the common label itself): round input/output label, judgment, proposal, open items |
| `mixed400/summary.csv` | 60 | arm × experiment aggregates |
| `mixed400/metrics.json` | — | full report: transitions, per-round detection, costs, paired comparisons |
| `numeric400/…` | same | same for the financial + rendered-FinQA cohort |
| `fin250/predictions.csv` | 500 | two arms: answer, gold, official relieved/exact, project label |
| `fin250/metrics.json` | — | official accuracy, exact, transitions, cost |
| `pure/per_sample.csv` | 1,050 | EVID vs vote (LEQ and GEQ checkpoints), per-item token target and actual |
| `pure/completion.json` | — | the 50-case budget-completion pass, with its own usage |
| `ablation/per_sample.csv` | 4,000 | five arms × 800 samples: answer, label, status, check verdict, gap type |
| `ablation/comparisons.csv` | 8 | McNemar vs FULL, Holm over the family of eight |
| `ablation/metrics.json` | — | per-arm accuracy, calls, comparison table |

Labels are `C` (correct), `W` (wrong), `A` (unsubmitted, kept in the denominator).
Project labels use the frozen rule: `display_match OR exec_match`. `fin250` uses the
official relieved metric as the main label and keeps the project label beside it.

## What is authoritative

* For the published numbers: these files. A fresh run may differ by a few items;
  temperature 0 is not a determinism guarantee across days or backends.
* For how a number was produced: the `metrics.json` of the same folder, which also
  records the freeze hash of the run it came from.

## Verify

```bash
python scripts/verify_reported_numbers.py
```

It prints one line per check (`paper=… computed=… ok`) and exits non-zero on any
mismatch. 56 checks: end-to-end by arm, by source; repair finals and transition
counts; FinTabNetQA relieved/exact; vote counts; all five ablation arms on both sets.

## Caveats worth knowing before quoting a number

* `mixed400` and `numeric400` share 174 questions. Do not add their accuracies
  together or treat them as independent replications.
* `fin250` is saturated for this model: a one-item difference moves the headline by
  0.4 points, and the paired comparison is reported with that in mind.
* The vote control is “at least this token budget”, not an exact match: a full image
  call cannot be split, so the actual spend overshoots the target by a median of
  22–39% depending on the set.
* The ablation’s null results are reported with the trigger rate; see
  `docs/scope.md`.
