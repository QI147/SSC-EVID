"""End-to-end EVID36 behaviour with a stubbed model: no network, no API key, no images.

The stub returns scripted JSON per call, so these tests pin the *control flow*:
which statuses are reachable, what the program refuses to accept, and that the
delivered answer is the one the last gate approved.
"""
import json
import unittest

from evid36 import evid_integrated as E36
from evid36 import fact_first as F36

ITEM = {"sample_id": "T1", "question": "How many units were shipped in 2011?",
        "image": "unused.png", "image_sha256": "0" * 64, "dataset": "test"}

OBSERVE = {"question_plan": {"answer_type": "number", "condition": "units shipped",
                             "selection_unit": "rows"},
           "scope": "rows 2-4, column 2011", "unresolved": [],
           "rows": [["row 3", "2011 / units", "1,200", 1200, None],
                    ["row 2", "2011 / units", "900", 900, None]],
           "operations": []}

INITIAL = {"answer": "1200", "reasoning": "Read 1,200 from the 2011 column.",
           "supporting_ids": ["i1"], "scope": "rows 2-4", "unresolved": []}


def response(obj):
    return {"status": "ok", "finish_reason": "stop", "content": json.dumps(obj),
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


class Scripted:
    """Return scripted objects in call order and record the prompts."""

    def __init__(self, script):
        self.script = list(script)
        self.prompts = []

    def __call__(self, arm, phase, system, prompt, image=None):
        self.prompts.append((phase, prompt))
        if not self.script:
            raise AssertionError("unexpected extra call: %s" % phase)
        return response(self.script.pop(0))


class EvidenceLoopTests(unittest.TestCase):
    def test_kept_when_support_is_complete(self):
        stub = Scripted([OBSERVE, INITIAL,
                         {"analysis": "1,200 is the 2011 value", "error_type": None,
                          "verdict": "correct", "evidence": [], "supporting_ids": ["i1"],
                          "scope": "row 3, 2011 column", "unresolved": [],
                          "gap": None, "action": None}])
        initial, _ = F36.initial(ITEM, stub)
        self.assertEqual(initial["answer"], "1200")
        out = E36.run(ITEM, initial, stub)
        self.assertEqual(out["status"], "KEPT_EVIDENCE_SUPPORTED")
        self.assertEqual(out["answer"], "1200")
        self.assertEqual(len(out["calls"]), 1)
        self.assertEqual(stub.script, [])

    def test_correction_requires_evidence_then_gate(self):
        stub = Scripted([
            OBSERVE, INITIAL,
            {"analysis": "2011 column was read from the 2010 header", "error_type": "binding",
             "verdict": "incorrect", "evidence": [], "supporting_ids": ["i1"],
             "scope": "row 3", "unresolved": [],
             "gap": {"type": "BINDING", "target": "row 3 header", "why": "year column ambiguous"},
             "action": "A_BIND"},
            {"evidence": [{"id": "a1", "location": "row 3 header", "content": "2010",
                           "source": "image"}], "scope": "row 3 only", "unresolved": []},
            {"verdict": "incorrect", "error_type": "binding", "analysis": "value belongs to 2010",
             "supporting_ids": ["a1"], "scope": "row 3", "unresolved": [],
             "answer": "1500", "reasoning": "2011 column holds 1,500 for row 3"},
            {"verdict": "correct", "analysis": "1,500 matches the 2011 cell",
             "supporting_ids": ["a1"], "scope": "row 3, 2011", "unresolved": []},
        ])
        initial, _ = F36.initial(ITEM, stub)
        out = E36.run(ITEM, initial, stub)
        self.assertEqual(out["status"], "CORRECTED_EVIDENCE_SUPPORTED")
        self.assertEqual(out["answer"], "1500")
        self.assertEqual([v["version"] for v in out["evidence_versions"]], [0, 1])

    def test_gate_blocks_unsupported_candidate(self):
        stub = Scripted([
            OBSERVE, INITIAL,
            {"analysis": "value may be from the wrong column", "error_type": "binding",
             "verdict": "incorrect", "evidence": [], "supporting_ids": ["i1"],
             "scope": "row 3", "unresolved": [],
             "gap": {"type": "BINDING", "target": "row 3", "why": "ambiguous"},
             "action": "A_BIND"},
            {"evidence": [{"id": "a1", "location": "row 3", "content": "2011 = 1,400",
                           "source": "image"}], "scope": "row 3", "unresolved": []},
            {"verdict": "incorrect", "error_type": "binding", "analysis": "should be 1,400",
             "supporting_ids": ["a1"], "scope": "row 3", "unresolved": [],
             "answer": "1400", "reasoning": "2011 cell reads 1,400"},
            {"verdict": "incorrect", "analysis": "cannot support 1,400 from the ledger",
             "supporting_ids": ["a1"], "scope": "row 3", "unresolved": []},
        ])
        initial, _ = F36.initial(ITEM, stub)
        out = E36.run(ITEM, initial, stub)
        self.assertEqual(out["status"], "FALLBACK_UNVERIFIED")
        self.assertEqual(out["answer"], "1200")          # initial kept, candidate rejected
        self.assertIn("final_not_supported", out["unresolved"])

    def test_correct_with_unresolved_is_rejected(self):
        stub = Scripted([
            OBSERVE, INITIAL,
            {"analysis": "looks right", "error_type": None, "verdict": "correct",
             "evidence": [], "supporting_ids": ["i1"], "scope": "row 3",
             "unresolved": ["year not checked"], "gap": None, "action": None},
        ])
        initial, _ = F36.initial(ITEM, stub)
        out = E36.run(ITEM, initial, stub)
        self.assertEqual(out["status"], "FALLBACK_UNVERIFIED")
        self.assertIn("correct_with_unresolved", out["unresolved"])

    def test_unknown_reference_is_not_accepted_as_support(self):
        stub = Scripted([
            {"question_plan": {"answer_type": "number"}, "scope": "OPEN", "unresolved": [],
             "rows": [["row 3", "2011", None, None, None]], "operations": []},
            {"answer": "1200", "reasoning": "guess", "supporting_ids": ["i1"],
             "scope": "row 3", "unresolved": []},
            {"analysis": "no verified observation", "error_type": "value", "verdict": "unknown",
             "evidence": [], "supporting_ids": [], "scope": "row 3",
             "unresolved": ["cell unreadable"], "gap": None, "action": None},
        ])
        initial, record = F36.initial(ITEM, stub)
        self.assertEqual(record["observation_normalization"]["status"], "COMPLETE")
        self.assertEqual(initial["answer"], "1200")
        out = E36.run(ITEM, initial, stub)
        self.assertEqual(out["status"], "FALLBACK_UNVERIFIED")

    def test_initial_missing_short_circuits(self):
        stub = Scripted([OBSERVE, {"answer": None, "reasoning": "not visible",
                                   "supporting_ids": [], "scope": "row 3",
                                   "unresolved": ["missing"]}])
        initial, _ = F36.initial(ITEM, stub)
        self.assertIsNone(initial)
        out = E36.run(ITEM, {"answer": None}, stub)
        self.assertEqual(out["status"], "INITIAL_MISSING")

    def test_observation_prompt_has_no_previous_answer(self):
        stub = Scripted([OBSERVE, INITIAL])
        F36.initial(ITEM, stub)
        observe_prompt, answer_prompt = stub.prompts[0][1], stub.prompts[1][1]
        self.assertIn("How many units", observe_prompt)
        self.assertNotIn("initial_answer", observe_prompt)
        self.assertIn("1200", answer_prompt)          # the answer call sees its own facts


if __name__ == "__main__":
    unittest.main(verbosity=2)
