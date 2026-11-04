# Reproducing the experiments

Two levels are possible:

* **Re-score the paper** (no key, no images): `python scripts/verify_reported_numbers.py`.
* **Re-run the trials** (key, images, money): everything below.

The harness is deliberately boring: one transport, one freeze, append-only shards,
one scorer. If a step feels magical, it is a bug.

---

## 0. Environment

Python 3.10+ (developed on 3.13). The method uses only the standard library; the
harness adds nothing beyond it. `numpy` is needed only by `repro/official_eval.py`
(the official FinTabNetQA normaliser); `jsonschema`/`pytest` are not required.

```bash
python -m venv .venv
. .venv/Scripts/activate            # Windows
# source .venv/bin/activate         # Linux / macOS
pip install -r requirements.txt
```

Offline checks first:

```bash
python -m unittest discover -s tests -v     # 32 tests, local stub endpoint
python scripts/preflight.py                 # manifests, gold coverage, request caps
```

`preflight` reports missing images until step 2 is done; that is expected.

## 1. Credentials and endpoint

The client speaks the OpenAI-compatible chat-completions API with an image part.
Copy `.env.example` to `.env` (never committed) or export the variables:

| Variable | Meaning | Default |
|---|---|---|
| `EVID_API_KEY` | bearer token | — (required) |
| `EVID_BASE_URL` | base URL, no trailing slash, `/chat/completions` is appended | DashScope compatible endpoint |
| `EVID_MODEL` | model alias | `qwen3.8-flash` |
| `EVID_ALLOWED_HOST` | refuse to send to any other host | empty (no pin) |
| `EVID_ROOT` | where `datasets/`, `runs/`, `reports/` live | repository root |
| `EVID_CRED_FILE` | alternative: a `key=value` file outside the repo | — |

The paper pinned one host and recorded the returned model name; pinning is optional
but recommended for a real batch (`EVID_ALLOWED_HOST=...`).

The model configuration is frozen into `runs/<set>/freeze.json`: model alias,
temperature 0, thinking off, `max_tokens=1200`, retry policy. Changing any of them
after a freeze is detected and refused.

## 2. Data

The repository ships manifests and gold, not images:

```
datasets/<set>/runtime.jsonl   sample_id, question, image path, image SHA256, source, source_group
datasets/<set>/eval.jsonl      sample_id, gold answer, exe_ans
datasets/<set>/manifest.json   cohort description
datasets/<set>/images/         you provide these
```

Add images with hash checking:

```bash
python scripts/import_images.py --dataset mixed400 --source D:/data/mixed400/images
python scripts/preflight.py --strict
```

Where the images come from, and their licences, is documented in `docs/data.md`.
If you change any image, its recorded SHA256 no longer matches and the run refuses
to start — that is intentional: the paper's input is a byte, not a table.

## 3. Freeze

A freeze hashes the code, the manifests, and every image, then hashes the whole
record. All later steps compare against it.

```bash
python -m repro.runner freeze --dataset mixed400
python -m repro.runner verify --dataset mixed400
```

`freeze` writes `runs/<set>/freeze.json` once (it refuses to overwrite). Use a fresh
`EVID_ROOT` or dataset copy for a new batch; do not "fix" a freeze by editing it.

## 4. Run a stage

One shard = one process, `k/n`. All shards of a stage share a file-locked request
cap, so the total cannot exceed the printed budget even if you start too many.

```bash
python scripts/run_experiment.py --dataset mixed400 --stage initial --shard 0/8 --authorize-paid
python scripts/run_experiment.py --dataset mixed400 --stage initial --shard 1/8 --authorize-paid
...
python scripts/run_experiment.py --dataset mixed400 --stage initial --merge
python -m repro.runner freeze-initial --dataset mixed400
```

Stages, in the only valid order:

| Order | Stage | Meaning | Needs |
|---|---|---|---|
| 1 | `initial` | one COMMON answer per item, frozen | freeze |
| 2 | `e2e` | DIRECT / REREAD2 / SSC / EVID independently | freeze |
| 3 | `repair` | SSC / EVID / KEEP / RR_MATCH from the same COMMON | `freeze-initial` |
| 4 | `fin250` | REREAD2 vs EVID on the public benchmark | freeze |
| 5 | `pure` | independent vote to a per-item token target | `pure_budget.json` (step 6) |
| 6 | `ablation` | FULL + four module deletions | freeze |

Run the shards in parallel (8 terminals, or `xargs -P 8`); each shard checkpoints
after every item.

```bash
python scripts/run_experiment.py --dataset mixed400 --stage e2e --shard 0/8 --authorize-paid --no-ssc
python scripts/run_experiment.py --dataset mixed400 --stage e2e --merge
```

`--no-ssc` is required unless you installed the SSC author's code (see
`docs/data.md`). EVID does not depend on it.

### Resume

Re-running the same shard continues where it stopped: finished items are read from
`results_s<k>.jsonl`, in-flight logical requests are replayed from the fingerprint
cache, and the quota counter is rebuilt from the ledger. A transport failure stops
the shard with `infrastructure interruption; cache preserved` instead of burning
quota in a loop.

## 5. Score

```bash
python -m repro.evaluate --dataset mixed400      # e2e + repair tables
python -m repro.evaluate --dataset numeric400
python scripts/score_all.py                       # every finished run
```

Outputs under `reports/`:

| File | Contents |
|---|---|
| `<set>/metrics.json` | transitions, per-round detection, costs, paired comparisons |
| `<set>/summary.csv` | one row per arm × experiment |
| `<set>/end_to_end.csv` | per-question answers and labels for every arm |
| `<set>/repair_rounds.csv` | per-question, per-round answers, judgments, proposals |
| `<set>/calls_full.csv` | the whole ledger, including reused rows |
| `fin250/metrics.json` | official relieved/exact, project labels, cost |
| `pure/results.json` | vote vs EVID, budget deviation |
| `ablation/metrics.json` | five-arm accuracy, McNemar vs FULL, Holm |

Scoring is offline and separate: the runner refuses to start if the scorer has been
imported into the model process, and the scorer never reads a ledger to decide a
label.

## 6. Budget-matched independent vote

The vote target is EVID's own token cost per question, taken from the `e2e` ledger:

```bash
python scripts/build_pure_budget.py --dataset mixed400   # writes datasets/mixed400/pure_budget.json
python -m repro.runner freeze --dataset mixed400         # the budget is part of this freeze
python scripts/run_experiment.py --dataset mixed400 --stage pure --shard 0/8 --authorize-paid
python scripts/run_experiment.py --dataset mixed400 --stage pure --merge
python -m repro.score_pure --datasets mixed400 numeric400
```

The budget must exist *before* the freeze of the batch that runs the vote, because a
budget that could change later would silently re-price the control. If a dataset is
already frozen without `pure_budget.json`, run the vote in a separate `EVID_ROOT`
whose manifest folder contains the budget.

If a question does not reach its target within 16 reads, the harness records
`reached=false` and keeps the last state; the paper later completed those 50 cases
in a separate pass. Do not select which cases to extend by correctness.

## 7. Ablation

```bash
python scripts/run_experiment.py --dataset mixed400 --stage ablation --shard 0/8 --authorize-paid
python scripts/run_experiment.py --dataset mixed400 --stage ablation --merge
python -m repro.score_ablation --datasets mixed400 numeric400
```

Each item is answered by all five arms; identical prompt prefixes are shared through
the cache, so a deletion that only changes the final gate cannot gain from a fresh
sample of the earlier stages.

## 8. Cost and time

From the paper's own ledger (frozen prices, CNY, estimate not invoice):

| Batch | Requests | Tokens | Estimate |
|---|---:|---:|---:|
| mixed400, EVID arm only | 1,261 | 2,726,050 | ¥2.64 |
| numeric400, EVID arm only | 1,241 | 3,016,905 | ¥2.81 |
| mixed400, all four e2e arms | ~3,300 | — | ~¥5.5 |
| whole 51 ledger (e2e + repair + initials) | 10,713 | 18,416,143 | ¥17.68 |
| fin250 two arms + reread control | 3,476 | 5,017,653 | ¥4.50 |
| module ablation, five arms | 4,215 | — | ¥8.99 |

A 400-item stage with 8 shards typically finishes in tens of minutes; the public
benchmark (250 items) is quicker. Image size dominates input tokens, so a different
renderer changes the bill even at the same request count.

## 9. Troubleshooting

| Symptom | Cause and action |
|---|---|
| `freeze drift: refusing requests` | code, manifest or image changed after the freeze. Restore the file, or start a new batch in a new `EVID_ROOT`. |
| `per-item role limit <arm>` | the loop tried one call more than its budget; inspect the trace of that item, do not raise the limit. |
| `batch request cap reached` | the stage budget is spent. Finish, score, and start a new batch if needed. |
| `SSC author code not found` | add `--no-ssc`, or place `prompts.py`/`parser.py` in `vendor/ssc_author/`. |
| `Endpoint host ... is not the pinned EVID_ALLOWED_HOST` | unset the pin or point `EVID_BASE_URL` at the pinned host. |
| numbers differ by a few items from the paper | expected for a fresh run: temperature 0 is not a determinism guarantee across days or backends. The frozen tables in `results/` are authoritative for the published numbers. |

## 10. Checking a rerun against the paper

`scripts/verify_reported_numbers.py` is written against the frozen tables. For a new
run, compare `reports/<set>/summary.csv` with `results/<set>/summary.csv`; the
difference should be small and is worth reporting as run-to-run variance rather than
tuned away.
