# Data, provenance and licences

## Cohorts

| Folder | Items | Composition | Input condition | Metric |
|---|---:|---|---|---|
| `datasets/mixed400` | 400 | FinTabNetQA 174 + VWTQ 226 | native table image | frozen project score (display OR execution match) |
| `datasets/numeric400` | 400 | original financial 200 (FinTabNetQA) + FinQA dev 200 | native images; FinQA is re-rendered with all pre/post text on one canvas | frozen project score |
| `datasets/fin250` | 250 | full public FinTabNetQA split, 205 tables | official images, bytes unchanged | official relieved + exact |

`mixed400` and `numeric400` share 174 question ids (800 executions, 626 distinct
questions). They are development cohorts, not independent generalisation sets; the
paper says so and does not pool them.

## What is in git

```
datasets/<set>/runtime.jsonl    sample_id, question, image (relative), image_sha256,
                                dataset/source metadata, source_group
datasets/<set>/eval.jsonl       sample_id, gold answer, exe_ans
datasets/<set>/manifest.json    cohort description
datasets/REBUILD_SPEC.json      upstream ids, selection flags, image hashes, rendering metadata
datasets/numeric400/rendering.json   canvas parameters for the FinQA renders
```

`runtime.jsonl` is the only file a model process may see. `eval.jsonl` is read by the
scorer alone; the runner raises if the scorer is imported.

Gold answers for FinTabNetQA and VWTQ are the benchmark's own labels. FinQA gold
comes from the dev split (`answer`, `exe_ans`), and the rendered input is generated
from the same record so the text context and the table cannot drift apart.

## What is not in git

**Images.** They are ~90 MB across the three paper cohorts plus ~7 MB of Chinese
renders, and their redistribution terms differ per source. The 30 MB
`datasets/_raw/TableEval-test.jsonl` upstream dump is also excluded (`_raw/` in
`.gitignore`); `scripts/build_zh_tableeval.py` rebuilds the Chinese cohort from a
local copy. Instead:

* every `runtime.jsonl` row carries the SHA256 of the exact image used;
* `scripts/import_images.py` copies a local image folder into
  `datasets/<set>/images/` and refuses a hash mismatch;
* `scripts/preflight.py --strict` re-checks every hash before any request.

Where to obtain them:

| Source | How | Licence (as declared by the source) |
|---|---|---|
| TableVQA-Bench (`fintabnetqa`, `vwtq` shards) | the public release of the benchmark used in the paper; `runtime.jsonl` records `source.qa_id` | CC-BY-4.0 per the dataset card |
| FinQA dev | the public FinQA repository, `dev.json`; re-rendered locally | MIT (code and data release) |
| FinTabNetQA 250 | the `fintabnetqa` split, unmodified | as above |

The rendered FinQA images can be rebuilt from `REBUILD_SPEC.json` plus
`rendering.json`; the canvas is 1600 px wide, Arial 21/23 px, 29 px line height. The
paper used the stored PNGs, not a re-render, so a rebuilt canvas may differ in
pixels — the recorded SHA is the authority.

## Additional cohorts (post-paper)

These were added after the camera-ready snapshot. They are **not** in the paper's
tables and are not read by `scripts/verify_reported_numbers.py`.

| Cohort | Items | Origin | Licence / status |
|---|---:|---|---|
| `zh_tableeval`, `zh_tableeval_qwen*` | 200 | rendered from `TableEval-test.jsonl` (Chinese markdown tables) | TableEval is Apache-2.0; cite Zhu et al., EMNLP 2025. Images excluded. |
| `zh_opt16_v36`, `_v40`, `_v41` | 16 each | task round-robin over `zh_tableeval`, one question per table, no model-outcome selection | **optimisation material, not held-out**; the three folders are byte-identical |
| `vtabfact250`, `vwtqsyn250`, `*_qwen` | 250 each | synthetic TableVQA shards | manifests and gold only |
| `mixed400_*` / `numeric400_*` (`dsflash`, `qwen38`, variants 37/38/39, `pilot8`) | 8–400 | second-backbone and prompt-contract subsets | manifests and gold only |
| `fin250`, `mixed400`, `numeric400` | paper cohorts | see above | unchanged |

All of them follow the same rule: `runtime.jsonl` (with `image_sha256`),
`eval.jsonl` and `manifest.json` are in git; images are not. Frozen summaries of
the runs are the three JSON files in `reports/` that `.gitignore` explicitly
re-includes; the rest of `reports/` and all of `runs/` stay local. The 16-item
pilot's per-question outputs are frozen in `experiments/zh_opt16/results/`.

## Traceability

`datasets/REBUILD_SPEC.json` is the join between this repository and the upstream
benchmarks. Each record contains:

* `sample_id` and the exact `question`;
* `image` and `image_sha256`;
* `source` (`pool`, `shard`/`split`, `qa_id`, and for FinQA the source `filename`);
* `source_group` and its basis (normalised table hash or exact input table hash);
* selection flags (`cohort`, `input_condition`, `experiment_family`, `order`).

Selection never used model outputs: the FinQA 200 were drawn by fixed hash over
input-only projections with at most one question per table, and the ids were frozen
before any label was attached.

## Third-party code

| Item | Location | Licence / status |
|---|---|---|
| official FinTabNetQA normaliser | `repro/official_eval.py` | Apache-2.0, copied from `allenai/allennlp-semparse` with attribution |
| SSC author prompts/parsers | **not redistributed**; loaded from `vendor/ssc_author/` | no licence file found in the checkout used; obtain it from the authors and place `prompts.py` + `parser.py` there |
| everything else | `evid36/`, `repro/`, `scripts/` | project owner's licence, see `LICENSE` |

`repro/ssc_baseline.py` is an adapter: it changes only the transport (image instead
of a serialised text table) and leaves the author's prompts, parsers and three-step
branching intact. Results from that arm are therefore "author method under an image
input condition", not a reproduction of the original text-table experiment.

## Privacy and safety

The cohorts contain public financial tables and encyclopedic tables; no personal data
was collected. Prompts contain only the question, the image and the model's own
previous outputs. Gold answers are never placed in a prompt: the runner and the
scorer are separate processes, and the runner refuses to start if `scoring` is
imported.

## Reproducing the cohort files

`REBUILD_SPEC.json` is sufficient to rebuild `runtime.jsonl` (it contains the same
questions, images and hashes), but the benchmark downloads themselves are not
mirrored here. If you rebuild a cohort, keep the `sample_id` values: every frozen
table in `results/` is keyed by them.
