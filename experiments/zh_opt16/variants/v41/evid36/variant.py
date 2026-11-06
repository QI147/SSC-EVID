"""Prompt contract variant.

``36`` is the published contract: the answer field is allowed to carry the full
requested answer, including qualifiers and parentheses, and the observation step
is allowed to list as many rows as the model finds relevant.

``37`` is the cross-backbone repair: the information content is identical, only
the packaging changes.  The answer must be the value exactly as the table prints
it (plus the printed unit), with no sentence, no parenthesised working and no
restated condition; the observation step is capped and must put the row that the
question's condition selects first.  Motivations are measured in
``实验二_端到端准确率/第二模型_原因分析.md``.

Selected once, at import, from ``EVID_PROMPT_VARIANT``; the value is recorded in
``freeze.json`` so a run can never be mistaken for the other contract.
"""
import os

VARIANT = os.environ.get("EVID_PROMPT_VARIANT", "36").strip() or "36"
