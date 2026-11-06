# Post-paper experiment archives

Two directories added after the camera-ready snapshot. Neither is part of the 56
numbers checked by `scripts/verify_reported_numbers.py`.

| Path | Contents | Status of claims |
|---|---|---|
| `paper/` | Source of the five internal packages that produced the paper's tables (51, 52, 54, 55, 59) | supports the paper; **archival source**, not a runnable second harness |
| `zh_opt16/` | 2026-11-05 Chinese optimisation pilot: 16 questions, three prompt variants, frozen per-question outputs | **not a held-out test**, not a significance result |

The published method stays in `evid36/`, the published tables in `results/`. To
actually re-run anything, use `repro/` and `scripts/` at the repository root
(`docs/reproduce.md`). `datasets/` holds the additional cohorts' manifests and the
16-item optimisation set; `reports/*.json` holds frozen summaries of the
second-backbone and Chinese-cohort runs.

Images, full call ledgers and raw benchmark dumps are deliberately excluded; see
`docs/data.md`. Nothing here changes the paper's numbers: with the default prompt
contract the offline tests and the number check still pass.
