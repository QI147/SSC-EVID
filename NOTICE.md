# Third-party notices

## Code

**`repro/official_eval.py`** — the FinTabNetQA normaliser and comparison helpers are
copied from [allenai/allennlp-semparse](https://github.com/allenai/allennlp-semparse),
Copyright 2017 The Allen Institute for Artificial Intelligence, licensed under the
Apache License, Version 2.0. The file keeps its attribution header. Only the
normalisation/comparison helpers are used.

**SSC baseline (not included).** The SSC comparison arm uses the original author's
prompts, parsers and three-step branching. That source is not redistributed here
because no licence file was found in the checkout that was used. Place the author's
`prompts.py` and `parser.py` in `vendor/ssc_author/` (or set `EVID_SSC_SRC`) to enable
the arm; `repro/ssc_baseline.py` only adapts the transport from a text table to an
image.

## Data (not redistributed)

* **TableVQA-Bench** (`fintabnetqa`, `vwtq` shards) — the dataset card declares
  CC-BY-4.0. Only question text, ids and image hashes are included here.
* **FinQA** — released under the MIT licence; the numeric400 cohort re-renders 200 dev
  questions with their pre/post text. Only the rendered image hashes and the question
  text are included.
* **FinTabNet / SEC filings** — the original financial table images derive from public
  company filings; redistribution terms should be reviewed before publishing images.
* **TableEval** (`zh_tableeval*` and `zh_opt16_*` cohorts) — `wenge-research/TableEval`,
  `TableEval-test.jsonl`, declared Apache-2.0; cite Zhu et al., EMNLP 2025. Only the
  question text, gold answers, rendered-image hashes and manifests are included;
  neither the rendered images nor the upstream dump are redistributed.

If you add images to this repository, you are responsible for the licence that applies
to them; `scripts/import_images.py` only checks hashes.

## Figures

`paper/figures/` and `paper/figure_data/` were produced by the authors from the frozen
tables in `results/`. Figure 2 overlays boxes on two benchmark table images; the
underlying pixels are not modified, and the transformation records are in
`paper/figure_data/fig2_transforms.json`.
