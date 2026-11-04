"""Observation-protocol tests: what the program accepts, repairs and refuses.

These are the load-bearing behaviours behind the paper's claim that a missing
row is never inferred from a truncated response and that a model's "I scanned the
whole table" is not a completeness certificate.
"""
import json
import unittest

from evid36 import fact_protocol as P


def ok(content, finish="stop"):
    return {"status": "ok", "finish_reason": finish, "content": content}


class ParseTests(unittest.TestCase):
    def test_single_object_rejects_two_objects(self):
        self.assertIsNone(P.single_object(ok('{"a":1}{"b":2}')))
        self.assertEqual(P.single_object(ok('```json\n{"a":1}\n```')), {"a": 1})

    def test_truncated_response_keeps_complete_rows_only(self):
        text = ('{"question_plan":{"answer_type":"number"},"scope":"rows 1-9",'
                '"unresolved":[],"rows":[["row 1","2011","10",10,null],'
                '["row 2","2011","20",20,null],["row 3","2011"')
        obj, status = P.observation(ok(text))
        self.assertEqual(status["status"], "RECOVERED_COMPLETE_RECORDS")
        self.assertEqual([f["content"] for f in obj["facts"]], ["10", "20"])
        self.assertTrue(obj["scope"].startswith("OPEN: incomplete response"))
        self.assertEqual(status["range_status"], "OPEN")

    def test_length_finish_is_not_parsed_as_complete(self):
        self.assertIsNone(P.single_object(ok('{"facts":[{"id":"i1"}]}', finish="length")))

    def test_unbound_record_is_unverified_not_guessed(self):
        text = json.dumps({"facts": [{"id": "i1", "location": "row 1", "content": "x",
                                      "source": "image"},
                                     "loose string"], "scope": "row 1", "unresolved": []})
        obj, status = P.observation(ok(text))
        self.assertEqual(obj["facts"][1]["source"], "unverified")
        self.assertTrue(obj["facts"][1]["binding_unresolved"])
        self.assertIn("unbound_record:2", status["issues"])

    def test_duplicate_ids_are_renamed_and_downgraded(self):
        text = json.dumps({"facts": [{"id": "i1", "location": "a", "content": "1", "source": "image"},
                                     {"id": "i1", "location": "b", "content": "2", "source": "image"}],
                           "scope": "a,b", "unresolved": []})
        obj, _ = P.observation(ok(text))
        self.assertEqual(obj["facts"][1]["id"], "i_recovered_2")
        self.assertEqual(obj["facts"][1]["source"], "unverified")
        self.assertTrue(obj["facts"][1]["identity_unresolved"])

    def test_empty_cell_is_marked_not_zero(self):
        text = json.dumps({"facts": [{"id": "i1", "location": "row 1", "content": "",
                                      "source": "image"}], "scope": "row 1", "unresolved": []})
        obj, _ = P.observation(ok(text))
        self.assertEqual(obj["facts"][0]["content"], "[EXPLICIT_EMPTY_CELL]")
        self.assertTrue(obj["facts"][0]["empty_cell_observation"])

    def test_missing_scope_and_plan_are_disclosed(self):
        text = json.dumps({"facts": [{"id": "i1", "location": "row 1", "content": "1",
                                      "source": "image"}], "unresolved": []})
        obj, status = P.observation(ok(text))
        self.assertTrue(obj["scope"].startswith("OPEN"))
        self.assertIn("scope_not_emitted", status["issues"])
        self.assertIn("question_plan_not_emitted", status["issues"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
