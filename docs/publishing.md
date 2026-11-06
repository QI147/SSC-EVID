# Publishing checklist

Done already: branch `main`, first commit `d5f9b89`, pushed to
`https://github.com/QI147/SSC-EVID` (public), MIT licence in `LICENSE`, author block
in `CITATION.cff` filled with the name only. What is left is below.

## 1. Decide before the first push

| Item | Action | Where |
|---|---|---|
| Licence | **done — MIT**; swap the file if you change your mind | `LICENSE` |
| Authors | add ORCID / affiliation (currently name only) | `CITATION.cff` |
| Repository URL | **done — `QI147/SSC-EVID`** | `CITATION.cff` |
| Double-blind | keep `paper/` to figures only (current state); do not add the manuscript or acknowledgements. The archived experiment source in `experiments/paper/` contains no author identity | `paper/`, `experiments/paper/` |
| Images | **decided — excluded**; manifests carry SHA256, `datasets/_raw/` excluded too | `docs/data.md`, `.gitignore` |
| SSC code | leave `vendor/ssc_author/` empty; the arm is optional | `NOTICE.md` |
| Secrets | confirm `.env` is untracked, no key in history | `git status`, `git log -p` |

## 2. First commit and push

```bash
git config user.name  "Your Name"
git config user.email "you@example.com"
git commit -m "SSC-EVID: method, harness, frozen results and reproduction docs"

# create an empty repository on GitHub first, then:
git remote add origin https://github.com/<you>/SSC-EVID.git
git push -u origin main
```

If you use the GitHub CLI: `gh repo create SSC-EVID --public --source=. --remote=origin --push`.

## 3. Repository settings worth five minutes

* **About**: one sentence — “Code and frozen results for SSC-EVID, evidence-driven
  repair for visual table QA”. Topics: `table-question-answering`,
  `vision-language-model`, `self-correction`, `reproducibility`.
* **Releases**: tag `v36.0` matching `CITATION.cff`. Attach the image archive there
  rather than in git if you decide to share images.
* **DOI**: enable the repository in Zenodo (or figshare) and put the DOI in
  `CITATION.cff` and the paper’s artifact statement. Reviewers can then cite the
  exact snapshot.
* **Issues**: an issue template asking for “command, dataset, first error line” saves
  you time; most failures are a missing image or a drifted freeze.

## 4. Verify a clean clone

```bash
git clone <url> /tmp/ssc-evid-check && cd /tmp/ssc-evid-check
python -m unittest discover -s tests -v
python scripts/verify_reported_numbers.py
```

Both must pass without a key and without images. If they do not, something was
committed that should have been ignored (usually `runs/` or a `.env`).

## 5. What reviewers will try first

1. `python scripts/verify_reported_numbers.py` — 56 checks against the paper.
2. Read `evid36/` — 440 lines, no dependencies.
3. Run one item end to end with their own key — `docs/reproduce.md` §4.
4. Look for the ablation parity test (`tests/test_ablation.py`) to see that “FULL” is
   the method and not a re-implementation.

Keep those four paths working; they carry most of the artifact’s credibility.

## 6. After acceptance

* Add the paper’s DOI and the final venue to `CITATION.cff`.
* Freeze a `paper-v1` tag; do not rewrite the tables in `results/` afterwards.
* If you re-run on a new model, add a new `results/<model>/` folder instead of
  replacing the old one — the paper’s claim is model-specific and dated.
