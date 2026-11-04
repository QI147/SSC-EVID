"""Scoring contracts: display precision is separate from execution tolerance.

The paper reports both, and a number can pass one and fail the other. These tests
freeze that behaviour, including the two percentage conventions found in the
mixed sources.
"""
import unittest

from repro import evaluate as EV
from repro import scoring


class DisplayAndExecutionTests(unittest.TestCase):
    def test_rounded_display_also_passes_execution(self):
        item = {"answer": "27.6", "exe_ans": 27.6}
        result = scoring.eval_one("s", "27.60", item)
        self.assertTrue(result["display_match"])
        self.assertTrue(result["exec_match"])

    def test_percent_display_passes_but_execution_misses(self):
        item = {"answer": "69.3%", "exe_ans": 0.69322}
        result = scoring.eval_one("s", "69.3%", item)
        self.assertTrue(result["display_match"])
        self.assertFalse(result["exec_match"])
        self.assertEqual(result["unit_mode"], "div")

    def test_fraction_answer_does_not_pass_a_percent_reference(self):
        item = {"answer": "69.3%", "exe_ans": 0.69322}
        result = scoring.eval_one("s", "0.693", item)
        self.assertFalse(result["display_match"])

    def test_plain_number_source_never_divides(self):
        item = {"answer": "27.6", "exe_ans": 27.6}
        self.assertEqual(scoring.reference_profile(item)["mode"], "same")
        self.assertFalse(scoring.eval_one("s", "0.276", item)["exec_match"])

    def test_text_answer_uses_normalised_strings(self):
        item = {"answer": "North America", "exe_ans": None}
        self.assertTrue(scoring.eval_one("s", "north america", item)["display_match"])
        self.assertFalse(scoring.eval_one("s", "Europe", item)["display_match"])

    def test_unsubmitted_is_wrong_not_missing(self):
        item = {"answer": "27.6", "exe_ans": 27.6}
        result = scoring.eval_one("s", None, item)
        self.assertFalse(result["display_match"])
        self.assertFalse(result["exec_match"])

    def test_nested_gold_shape_is_supported(self):
        item = {"gold": {"official": {"answer": "12", "exe_ans": 12}}}
        self.assertTrue(scoring.eval_one("s", "12", item)["display_match"])


class TransitionTests(unittest.TestCase):
    def test_transition_matrix_counts_all_nine_cells(self):
        initial = {"a": "C", "b": "C", "c": "C", "d": "W", "e": "W"}
        final = {"a": "C", "b": "W", "c": "A", "d": "C", "e": "A"}
        stat = EV.transitions(initial, final)
        self.assertEqual(stat["matrix"]["C->C"], 1)
        self.assertEqual(stat["corrected"], 1)
        self.assertEqual(stat["corrupted"], 1)
        self.assertEqual(stat["correct_lost_to_unsubmitted"], 1)
        self.assertAlmostEqual(stat["repair_rate"], 1 / 2)
        self.assertAlmostEqual(stat["corruption_rate"], 1 / 3)
        self.assertAlmostEqual(stat["correct_loss_rate"], 2 / 3)

    def test_mcnemar_is_exact_and_symmetric(self):
        self.assertAlmostEqual(EV.mcnemar(0, 0), 1.0)
        self.assertAlmostEqual(EV.mcnemar(10, 0), 2 / 1024)
        self.assertAlmostEqual(EV.mcnemar(0, 10), 2 / 1024)
        self.assertAlmostEqual(EV.mcnemar(3, 3), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
