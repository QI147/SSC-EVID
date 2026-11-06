# Archival source of the paper's experiments

This directory holds the **source files** of the five internal experiment packages
that produced the tables quoted in the paper. It is provenance, not a second
harness: the runnable, parameterised code is `repro/` + `scripts/` at the
repository root (`docs/reproduce.md`).

| Here | Internal package | Paper item |
|---|---|---|
| `51_end_to_end_400/` | `51_SSC_EVID_400统一对照` | table 2a end-to-end, table 4 shared-initial repair |
| `52_fin250_reread/` | `52_论文实验一与预算重读` | table 2b FinTabNetQA 250, appendix B reread |
| `54_independent_vote/` | `54_纯重读投票对照` | §6.2 independent vote to the matched token budget |
| `55_budget_completion/` | `55_纯重读预算补齐` | the 50-case budget-completion pass folded into `results/pure/` |
| `59_module_ablation/` | `59_实验四端到端模块消融` | table 5, five-arm module ablation |

## What was copied

Top-level `*.py`, the package `README.md` / design notes, `protocol*.json`,
`freeze.json`, `SOURCE_PROVENANCE.json` (SHA256 of each copied file), the small
budget provenance files, and `52_fin250_reread/infra/run_guarded.py`. 67 archived
files, about 600 KB, plus this README. The archived `test_*.py` files are part of
that provenance and are **not** part of the offline suite (`python -m unittest
discover -s tests`).

## What was left out, and why

| Excluded | Reason |
|---|---|
| `runs/`, `reports/`, call ledgers, per-call receipts | ~1 GB of regenerable output; the frozen, checkable results are in `results/` |
| `datasets/` inside each package | duplicates the manifests already in `datasets/` |
| `methods/evid36`, `methods/evid50` | copies of the method; the published one is `evid36/` at the root |
| `vendor/ssc_author/`, `upstream/official_eval.py` | author code without a redistributable licence; the Apache-2.0 normaliser is `repro/official_eval.py` |
| `*.cmd`, `build_workbook.mjs`, `启动说明.md` | local launch wrappers with machine-specific paths |
| packages 53, 56, 57, 58, 60 and the P1/P2/P3 pre-registrations | superseded or never executed; `docs/scope.md` records the reasons |

## Redactions

The internal packages hard-coded a private inference endpoint and absolute paths
on the author's machine. Before publishing, those were replaced with
`example.invalid`, `<internal-root>`, `<local-runner>` and `<writing-package>`.
Credentials were already read from `BAILIAN_API_KEY` / `BAILIAN_CRED_FILE`
environment variables; no key or credential file is included. Everything else is
byte-for-byte the internal source.

Consequence: these files **will not run as-is** — a few helpers point at
`<internal-root>` and expect the internal data trees. They are here so a reviewer
can see exactly which scorer, protocol and runner produced each table. Use the
root harness to reproduce the numbers.

## Double-blind

The manuscript (which contains author identity and acknowledgements) is not in
this repository. `paper/` still holds figures and figure data only; the figure
redraw script is `paper/scripts/plot_figures.py`.
