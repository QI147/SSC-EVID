# Redrawing the paper's figures

`plot_figures.py` rebuilds figures 3, 4, 5, 6 and S1 from the frozen CSVs in
`../figure_data/`, writing SVG, PDF and 300 dpi PNG into `../figures/`. Those are
exactly the files shipped in `paper/`, so a reviewer can redraw a figure without
the model, the images or an API key.

```bash
pip install matplotlib numpy     # optional; not needed by the method or harness
python paper/scripts/plot_figures.py
```

The script needs a CJK font for the Chinese labels: Microsoft YaHei / SimHei on
Windows, Noto Sans CJK on Linux. Without one, matplotlib falls back to DejaVu
Sans and the Chinese glyphs will be missing; the numeric data and layout are
unaffected.

Not included, on purpose:

* `verify_and_extract.py`, `build_data_sources.py`, `plot_fig1_fig2.py` — they read
  the internal experiment trees and absolute paths that are not in this repository.
* `build_docx.py`, `render_check.ps1` — manuscript typesetting via Word COM.
* The manuscript itself — it carries author identity; `paper/` stays figures-only
  for double-blind review.

Number-level verification is `scripts/verify_reported_numbers.py` against
`results/`, not these plotting scripts.
