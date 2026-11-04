"""The reference ablation arm must be the method, not a re-implementation.

``run_chain`` exists so that the four deletions can be expressed as switches. For
FULL those switches select the original prompts and flow, so the test below runs
both implementations against the same stubbed responses and compares the whole
trace.
"""
import json
import unittest

from evid36 import evid_integrated as E36
from evid36 import fact_first as F36
from repro import ablation

try:                                    # `python -m unittest discover -s tests`
    from tests.test_evid36_flow import INITIAL, ITEM, OBSERVE, Scripted
except ImportError:                     # `python tests/test_ablation.py`
    from test_evid36_flow import INITIAL, ITEM, OBSERVE, Scripted

CHECK_CORRECT = {"analysis": "1,200 is the 2011 value", "error_type": None,
                 "verdict": "correct", "evidence": [], "supporting_ids": ["i1"],
                 "scope": "row 3, 2011 column", "unresolved": [], "gap": None, "action": None}

CHECK_BINDING = {"analysis": "wrong year column", "error_type": "binding",
                 "verdict": "incorrect", "evidence": [], "supporting_ids": ["i1"],
                 "scope": "row 3", "unresolved": [],
                 "gap": {"type": "BINDING", "target": "row 3 header", "why": "ambiguous"},
                 "action": "A_BIND"}

ACQUIRED = {"evidence": [{"id": "a1", "location": "row 3 header", "content": "2010",
                          "source": "image"}], "scope": "row 3 only", "unresolved": []}

REPAIR = {"verdict": "incorrect", "error_type": "binding", "analysis": "value belongs to 2010",
          "supporting_ids": ["a1"], "scope": "row 3", "unresolved": [],
          "answer": "1500", "reasoning": "2011 column holds 1,500 for row 3"}

RECHECK = {"verdict": "correct", "analysis": "1,500 matches the 2011 cell",
           "supporting_ids": ["a1"], "scope": "row 3, 2011", "unresolved": []}


def run_both(script):
    reference = Scripted(script)
    initial, _ = F36.initial(ITEM, reference)
    expected = E36.run(ITEM, initial, reference)

    candidate = Scripted(script)
    initial2, _ = F36.initial(ITEM, candidate)
    actual = ablation.run_chain(ITEM, initial2, candidate, "FULL")
    return expected, actual, reference, candidate


class ParityTests(unittest.TestCase):
    def test_full_arm_matches_method_on_keep(self):
        expected, actual, ref, cand = run_both([OBSERVE, INITIAL, CHECK_CORRECT])
        self.assertEqual(json.dumps(expected, sort_keys=True, ensure_ascii=False),
                         json.dumps(actual, sort_keys=True, ensure_ascii=False))
        self.assertEqual([p[1] for p in ref.prompts], [p[1] for p in cand.prompts])

    def test_full_arm_matches_method_on_correction(self):
        script = [OBSERVE, INITIAL, CHECK_BINDING, ACQUIRED, REPAIR, RECHECK]
        expected, actual, ref, cand = run_both(script)
        self.assertEqual(expected["status"], "CORRECTED_EVIDENCE_SUPPORTED")
        self.assertEqual(json.dumps(expected, sort_keys=True, ensure_ascii=False),
                         json.dumps(actual, sort_keys=True, ensure_ascii=False))
        self.assertEqual([p[1] for p in ref.prompts], [p[1] for p in cand.prompts])

    def test_wrong_action_is_normalised_from_gap_type(self):
        script = [OBSERVE, INITIAL, {**CHECK_BINDING, "action": "A_VALUE"},
                  ACQUIRED, REPAIR, RECHECK]
        _, actual, _, _ = run_both(script)
        events = [e for e in actual["events"] if e["phase"] == "action_normalized"]
        self.assertEqual(events[0]["selected_action"], "A_BIND")

    def test_no_acquisition_arm_skips_the_call(self):
        stub = Scripted([OBSERVE, INITIAL, CHECK_BINDING,
                         {**REPAIR, "answer": "1200", "verdict": "unknown"}])
        initial, _ = F36.initial(ITEM, stub)
        out = ablation.run_chain(ITEM, initial, stub, "NO_ACQ")
        self.assertEqual(out["status"], "FALLBACK_UNVERIFIED")
        self.assertEqual(stub.script, [])
        self.assertTrue(any(e["phase"] == "acquisition_skipped" for e in out["events"]))

    def test_no_gate_arm_accepts_after_structural_checks(self):
        stub = Scripted([OBSERVE, INITIAL, CHECK_BINDING, ACQUIRED, REPAIR])
        initial, _ = F36.initial(ITEM, stub)
        out = ablation.run_chain(ITEM, initial, stub, "NO_GATE")
        self.assertEqual(out["status"], "CORRECTED_EVIDENCE_SUPPORTED")
        self.assertEqual(out["answer"], "1500")
        self.assertEqual(stub.script, [])

    def test_prompt_differences_are_registered(self):
        stages = {(d["arm"], d["stage"]) for d in ablation.PROMPT_DIFFS}
        self.assertIn(("NO_FACT_FIRST", "initial"), stages)
        self.assertIn(("GENERIC_ACQ", "acquire"), stages)
        self.assertIn(("NO_GATE", "recheck"), stages)
        self.assertEqual(len(ablation.ARMS), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
