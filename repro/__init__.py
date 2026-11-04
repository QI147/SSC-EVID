"""Reproduction harness for SSC-EVID (transport, freeze, trials, scoring).

The method itself lives in the sibling package ``evid36`` and is independent of
this package: the harness only supplies ``invoke`` and records what happened.
"""

__all__ = ["client", "common", "config", "evaluate", "runner", "scoring", "ssc_baseline"]
