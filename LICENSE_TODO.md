# Licence decision — required before publishing

No licence is granted by this repository yet. The code was written by the project
owner, and choosing a licence is the owner's decision, not a packaging detail. Until
one is added, default copyright applies and others may not reuse the code.

Pick one and save it as `LICENSE`:

| Option | Text | Fits when |
|---|---|---|
| MIT | <https://opensource.org/license/mit> | you want maximum reuse with attribution |
| Apache-2.0 | <https://www.apache.org/licenses/LICENSE-2.0> | you want a patent grant and explicit modification notices |
| CC-BY-4.0 for docs + MIT for code | two files | you want the prose/figures reusable separately |

Notes that must hold whichever you choose:

* `repro/official_eval.py` stays Apache-2.0; keep its header and list it in
  `NOTICE.md` (already done).
* The SSC author code is **not** covered: it is not in this repository and must be
  obtained separately.
* A code licence does not grant rights to the benchmark images. `docs/data.md`
  records the per-source terms; add a data licence note if you publish images.
* Figures in `paper/` are the authors' own work, derived from the frozen tables.

When you add `LICENSE`, also update the last section of `README.md` and the
`license:` field of `CITATION.cff`.
