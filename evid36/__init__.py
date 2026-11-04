"""EVID36 — evidence-driven repair for visual table question answering.

Three modules, unchanged from the frozen version that produced the paper's results:

* ``fact_protocol``  — compact observation protocol, strict single-object JSON
  parsing, and loss-aware recovery of complete records from truncated output.
* ``fact_first``     — observation-first initial answering plus a small,
  operand-checked auxiliary calculator.
* ``evid_integrated``— the repair loop: support check, gap->action acquisition,
  evidence versioning, candidate reassessment and a final gate.

Design split (see docs/method.md): the language model owns table semantics,
diagnosis and answer judgment; the program owns the evidence ledger, reference
and schema checks, explicit arithmetic, call budgets and the audit trail.

Nothing in this package performs I/O: an ``invoke(arm, phase, system, prompt,
image)`` callable is supplied by the caller, which lets the same code run against
a hosted model, a local model, or an offline stub (see tests/test_evid36_flow.py).
"""

__version__ = "36.0"
__all__ = ["evid_integrated", "fact_first", "fact_protocol"]
