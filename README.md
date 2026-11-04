# SSC-EVID · evidence-driven repair for visual table QA

**[Results](#reported-results)** · **[Quick start](#quick-start-no-key-no-images-1-minute)** ·
**[Reproducing a run](#reproducing-a-run)** · **[Method](#layout-of-the-method-package)** ·
**[Figures](paper/)** · **[Citation](#licence-and-citation)** ·
**[What is not here](#what-is-deliberately-not-here)**

*Manuscript under review. The code, the per-question outputs and the figures are
released so the reported numbers can be checked — the checks below run in about a
minute with no API key and no images.*

Code, frozen per-question results and reproduction steps for the paper
**“SSC-EVID: organising facts and traceable evidence acquisition for visual table
question answering”**.

The method records what the model actually observed, diagnoses *which support
relation* a candidate answer is missing, acquires exactly that observation, and
re-opens the candidate only in front of the updated evidence ledger. The language
model owns table semantics and judgments; the program owns the ledger, the schema
and reference checks, the arithmetic, the call budget and the audit trail.

```
table image + question
        │
        ├─ 1 observe facts ─────────────► evidence ledger  (i1, i2, …)
        ├─ 2 initial answer ────────────► candidate a0 + cited ids
        ├─ 3 support check ─────────────► correct | incorrect | unknown
        │        └─ gap {type, target, why} ─► 4 directed acquisition (a1, …)
        │                                          │ ledger version 1
        └─ 5 reassess (new candidate) ◄────────────┘
                 └─ final gate: only *correct with support and no open items* is delivered
```

## What is in this repository

| Path | Contents |
|---|---|
| `evid36/` | **The method**, three modules, byte-identical to the version that produced the paper’s numbers |
| `repro/` | Transport, freeze, trials, baselines, ablation arms, scoring |
| `scripts/` | Command-line entry points (preflight, run, score, verify, prompt dump) |
| `datasets/` | Question/image manifests and gold files for the three cohorts (images not included) |
| `results/` | Frozen per-question predictions and the tables quoted in the paper |
| `docs/` | Method, reproduction, experiment map, data/licensing, prompts, scope |
| `tests/` | 32 offline tests: protocol, scoring, ablation parity, full harness with a stub model |
| `paper/` | Camera-ready figures (SVG/PDF/PNG) and the CSV behind each figure |

Figures carry Chinese labels because the manuscript is in Chinese; `paper/figures/`
holds each one as SVG, PDF and PNG, and `paper/figure_data/` holds the CSV it was
drawn from. Open the SVG or PDF to zoom without loss — the README keeps the flow
above as text so it stays readable in a terminal and in dark mode.

## Reported results

Frozen project scoring, denominator 400 (250 for FinTabNetQA), unsubmitted answers
stay in the denominator. `DIRECT` reads once, `REREAD2` reads twice and keeps the
second read, `SSC` is the author’s three-step self-correction adapted to images,
`EVID` is this method.

| Question set | DIRECT | REREAD2 | SSC | **EVID** | Independent vote (matched budget) |
|---|---:|---:|---:|---:|---:|
| mixed400 (FinTabNetQA 174 + VWTQ 226) | 76.25% (305) | 78.25% (313) | 80.25% (321) | **87.50% (350)** | 76.00% (304) |
| numeric400 (financial 200 + rendered FinQA 200) | 68.50% (274) | 71.25% (285) | 77.00% (308) | **81.25% (325)** | 70.00% (280) |
| FinTabNetQA 250 — official relieved | — | 98.80% (247) | — | **98.40% (246)** | 98.00% (245) |
| FinTabNetQA 250 — official exact | — | 225 | — | **231** | — |

Shared-initial repair (both arms start from the same frozen answer):

| Set | Arm | initial correct | final correct | wrong→right | right→wrong |
|---|---|---:|---:|---:|---:|
| mixed400 | SSC | 323 | 341 | 22 | 4 |
| mixed400 | **EVID** | 323 | **346** | **27** | 4 |
| numeric400 | SSC | 301 | 316 | 18 | 3 |
| numeric400 | **EVID** | 301 | **325** | **26** | 2 |

Module ablation, end to end (FULL re-run in that batch: 352/400 and 327/400):

| Arm | mixed400 | numeric400 | Holm p (family of 8) |
|---|---:|---:|---:|
| FULL | 352 | 327 | — |
| no fact-first initial | 350 | 322 | 1.00 |
| directed action → generic re-observation | 354 | 326 | 1.00 |
| no new acquisition | 352 | 327 | 1.00 |
| no final gate | 354 | 326 | 1.00 |

The paper reads these zeros with the trigger rate: 92.6% of items stop at the
check, only 6.1% enter acquisition, so the ablation has little resolution for
three of the four modules. “Not detected” is not “no effect”.

`python scripts/verify_reported_numbers.py` recomputes **all 56 numbers above**
from `results/` and fails on any mismatch. It needs no key and no images.

Where each table comes from — the commands rebuild a table from a finished run
under `runs/`; `verify_reported_numbers.py` instead recomputes the published
numbers from the frozen `results/`, so it works on a machine that never ran a model.

| Table | Frozen file | Rebuild with |
|---|---|---|
| Main results (end to end) | `results/mixed400/end_to_end.csv`, `results/numeric400/end_to_end.csv` | `python scripts/score_all.py` |
| Shared-initial repair | `results/*/repair_rounds.csv` | `python scripts/score_all.py` |
| FinTabNetQA 250 (official metric) | `results/fin250/predictions.csv` | `python -m repro.score_fin250` |
| Independent vote (matched budget) | `results/pure/per_sample.csv` | `python -m repro.score_pure` |
| Module ablation | `results/ablation/per_sample.csv` | `python -m repro.score_ablation` |
| All 56 quoted numbers | `results/` (read-only) | `python scripts/verify_reported_numbers.py` |

## Quick start (no key, no images, ~1 minute)

```bash
python -m venv .venv
. .venv/Scripts/activate          # Windows;  source .venv/bin/activate on Linux/macOS
pip install -r requirements.txt

python -m unittest discover -s tests -v      # 32 tests, stub model only
python scripts/verify_reported_numbers.py    # 56/56 checks against the paper
```

## Reproducing a run

Full instructions, shard commands, resume semantics and cost: **`docs/reproduce.md`**.
The short version:

```bash
cp .env.example .env            # then edit: key + OpenAI-compatible base URL
python scripts/preflight.py --strict            # manifests, image hashes, request caps
python scripts/import_images.py --dataset mixed400 --source <your image folder>

python -m repro.runner freeze --dataset mixed400
python scripts/run_experiment.py --dataset mixed400 --stage initial --shard 0/8 --authorize-paid
# … shards 1/8 … 7/8, then
python scripts/run_experiment.py --dataset mixed400 --stage initial --merge
python -m repro.runner freeze-initial --dataset mixed400

python scripts/run_experiment.py --dataset mixed400 --stage e2e    --shard 0/8 --authorize-paid --no-ssc
python scripts/run_experiment.py --dataset mixed400 --stage repair --shard 0/8 --authorize-paid --no-ssc
python scripts/score_all.py
```

`--no-ssc` skips the SSC arm, whose author code is not redistributed here
(`docs/data.md` explains how to add it). Every run needs `--authorize-paid`; the
harness never spends silently. A request budget is enforced by a file lock shared
by all shards, and a restart resumes from the ledger instead of re-sampling.

## What is deliberately not here

* **Images.** Table images come from public benchmarks with their own licences; the
  repository ships manifests with SHA256 per image and an import script that
  refuses a hash mismatch. See `docs/data.md`.
* **The SSC author’s source.** No licence was found in that checkout, so the adapter
  loads it from `vendor/ssc_author/` if you provide it.
* **Internal experiment packages** that do not support a claim in the paper: the
  44/50 variants, the P1/P2/P3 designs that were never executed, the
  reread-with-own-history control, and the intermediate pilot packages. `docs/scope.md`
  lists each one and where its frozen output lives.

## Licence and citation

The code is released under the **MIT licence** — see `LICENSE`. It covers the
authors' code only: `repro/official_eval.py` stays Apache-2.0 (it is derived from
`allenai/allennlp-semparse`), the SSC comparison arm is not redistributed, and the
benchmark table images are not included at all. Details in `NOTICE.md` and
`docs/data.md`.

```bibtex
@misc{qi2026sscevid,
  title        = {SSC-EVID: organising facts and traceable evidence acquisition
                  for visual table question answering},
  author       = {Qi, Kai},
  year         = {2026},
  note         = {Code and frozen results, version 36.0},
  howpublished = {\url{https://github.com/QI147/SSC-EVID}}
}
```

`CITATION.cff` carries the same metadata, so GitHub's *Cite this repository*
button works without copying the block above.

## Layout of the method package

| File | Responsibility |
|---|---|
| `evid36/fact_protocol.py` | compact observation format, strict single-object JSON, loss-aware recovery of *complete* records from truncated output |
| `evid36/fact_first.py` | observation-first initial answer, auxiliary calculator over explicit operands |
| `evid36/evid_integrated.py` | support check → gap→action → ledger versioning → reassessment → final gate, with schema/reference enforcement and fallbacks |

The method imports nothing outside the standard library and performs no I/O: the
caller supplies `invoke(arm, phase, system, prompt, image)`. That is why the whole
loop can be tested offline against scripted responses (`tests/test_evid36_flow.py`).
