# Scope: what this repository contains, and what it deliberately leaves out

The paper reports a frozen development evaluation, not a leaderboard entry. This
repository is the subset of the internal project that supports every claim in the
paper, plus the code needed to re-run it.

## Included

| Experiment | Why it is here | Where |
|---|---|---|
| Four-arm end-to-end (DIRECT / REREAD2 / SSC / EVID) | main result, table 2a | `repro/runner.py::run_e2e` |
| Shared-initial repair, two rounds | separates repair from initial-state quality, table 4 | `repro/runner.py::repairs` |
| FinTabNetQA 250, two arms | public benchmark, official metric, table 2b | `repro/runner.py::fin250_trial` |
| Independent vote to a per-item token target | controls “is the gain just more reading?”, §6.2 | `repro/runner.py::pure_trajectory` |
| Five-arm module ablation | tests each module end to end, table 5 | `repro/ablation.py` |
| Frozen per-question tables | lets a reviewer recompute all reported numbers | `results/` |

## Excluded, with reasons

| Excluded | Reason | Where the frozen output lives (outside this repository) |
|---|---|---|
| EVID 44/50 variants (obligation audit, supersede relations, auxiliary metrics, initial self-check) | designed but not validated; the paper does not claim them | internal packages 44–50 |
| P1 / P2 / P3 pre-registered designs | never executed | internal design notes |
| Reread with the model’s own history (appendix B) | a different question (“strong control”), needs a per-item reread budget derived from the repair stage; including it without its budget file would invite misreading | internal package 52 (`followup.py` stage `*_rr`) |
| Budget-completion pass for 50 vote cases | folded into the frozen vote table: `results/pure/per_sample.csv` already contains the completed state for those 50 items | internal package 55 |
| Packages 56/57/58 | superseded by the five-arm package (59) that the paper reports | internal packages 56–58 |
| Package 60 (SSC single-component increment) | not executed at the time of the paper | internal package 60 |
| Pilot packages 13–43 | development history, no claim depends on them | internal archive |
| Images, full call ledgers | size and licences; manifests + hashes are shipped instead | private archive |

## Differences from the internal packages

The method is byte-identical (`md5` recorded in `evid36/README.md`). The harness was
edited where a private value would have leaked or a path would not exist on another
machine:

| Change | Internal | Here | Effect on results |
|---|---|---|---|
| Endpoint | hard-coded private host | `EVID_BASE_URL` + optional `EVID_ALLOWED_HOST` pin | none; the same host can be pinned |
| Paths | absolute `D10/01_代码/...` | relative to the repository, `EVID_ROOT` override | none |
| Credentials | file at a fixed path | environment or `EVID_CRED_FILE` | none |
| EVID50 arm | present in repair | removed | none: table 4 reports SSC/EVID only |
| SSC arm | always run | `--no-ssc` skips it when the author code is absent | none for EVID |
| Denominator | hard-coded 400 | length of the runtime manifest | none |
| Transport retries | receipts, up to two extra attempts | same policy, recorded as `:transport<k>` scopes | request counts match the stated policy |
| Receipts | per-call receipt files | cache + ledger (same identity fields) | resume semantics unchanged |

## Boundaries the paper states, and the code keeps

* **No semantic truth in the program.** The program never decides whether an answer
  is right; it decides whether the record is well formed and whether the citation
  exists.
* **`correct` is not a certificate.** A `correct` verdict with an open obligation is
  rejected; a model’s claim that it scanned the whole table is not evidence of
  coverage.
* **No unit verification claim.** The official relieved metric accepts magnitude
  variants. The paper reports a unit/format sensitivity separately, and so does the
  scoring code (`display_match` and `exec_match` are separate fields).
* **Single run per arm.** Temperature 0 is not a determinism guarantee. Intervals in
  the paper are table-clustered single-run intervals; run-to-run variance is not
  estimated.
* **Ablation is underpowered.** 92.6% of items stop at the check and only 6.1% enter
  acquisition, so three of the four deletions are rarely exercised. The reported
  Holm p = 1.00 is “not detected at this scale”, not “no effect”.

## If you extend this

* Add a new arm by writing a `run_*` function in `repro/runner.py` and a `LIMITS`
  entry; keep the scorer separate.
* Add a new gap type by extending `ACTIONS` and `DIRECTIVES` together — the action is
  derived from the gap type, so an unused directive is dead code and a missing one is
  a silent hole.
* Never change `evid36/` and then re-use an old freeze; the freeze hashes the method.
