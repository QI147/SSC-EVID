"""Harness smoke test: a local stub endpoint, a two-item cohort, real files.

This exercises the parts a reviewer actually runs: freeze, sharded execution,
merge, common-initial freeze, repair, and scoring. The model is replaced by a
tiny HTTP server, so the test needs no key and no network.

It also documents the request shapes: every prompt the harness sends is matched
here, which is why an unexpected prompt fails the test instead of returning junk.
"""
import base64
import json
import os
import shutil
import struct
import tempfile
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

OBSERVE = {"question_plan": {"answer_type": "number"}, "scope": "row 2", "unresolved": [],
           "rows": [["row 2", "2011 / units", "1,200", 1200, None]], "operations": []}
INITIAL = {"answer": "1200", "reasoning": "read 1,200 from the 2011 column",
           "supporting_ids": ["i1"], "scope": "row 2", "unresolved": []}
CHECK = {"analysis": "the 2011 cell reads 1,200", "error_type": None, "verdict": "correct",
         "evidence": [], "supporting_ids": ["i1"], "scope": "row 2", "unresolved": [],
         "gap": None, "action": None}


def png_bytes():
    """A valid 1x1 RGB PNG, built without Pillow."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\xff\xff"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def answer_for(prompt):
    """Map a prompt to a stub response, asserting that the prompt is recognised."""
    if "Every rows entry below is an IMAGE OBSERVATION" in prompt:
        return json.dumps(OBSERVE)
    if "produce an initial" in prompt and "Use your OWN observed facts" in prompt:
        return json.dumps(INITIAL)
    if "YOUR OWN initial answer" in prompt:
        return json.dumps(CHECK)
    if "Execute ONLY the specified evidence action" in prompt:
        return json.dumps({"evidence": [], "scope": "row 2", "unresolved": []})
    if "reassess the INITIAL" in prompt:
        return json.dumps({"verdict": "correct", "error_type": None, "analysis": "unchanged",
                           "supporting_ids": ["i1"], "scope": "row 2", "unresolved": [],
                           "answer": "1200", "reasoning": "confirmed"})
    if "Check the candidate against" in prompt:
        return json.dumps({"verdict": "correct", "analysis": "confirmed",
                           "supporting_ids": ["i1"], "scope": "row 2", "unresolved": []})
    if "Answer the image question once" in prompt:
        return json.dumps({"answer": "1200", "reasoning": "table shows 1,200"})
    if "Read the ORIGINAL image and question again" in prompt:
        return "1200"
    if "Answer the question using only the supplied table image" in prompt:
        return "1200"
    raise AssertionError("unrecognised prompt:\n" + prompt[:400])


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        prompt = body["messages"][0]["content"][-1]["text"]
        payload = {"model": "mock-vlm",
                   "choices": [{"message": {"content": answer_for(prompt)}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 120, "completion_tokens": 20}}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Stub)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.tmp = Path(tempfile.mkdtemp(prefix="sscevid-"))
        for name in ("datasets", "runs", "reports"):
            (cls.tmp / name).mkdir()
        images = cls.tmp / "datasets" / "demo" / "images"
        images.mkdir(parents=True)
        blob = png_bytes()
        runtime, gold = [], []
        for index in range(2):
            sid = "D%d" % index
            path = images / (sid + ".png")
            path.write_bytes(blob)
            import hashlib
            runtime.append({"sample_id": sid, "dataset": "fintabnetqa", "source_group": "g1",
                            "question": "How many units were shipped in 2011?",
                            "image": "datasets/demo/images/%s.png" % sid,
                            "image_sha256": hashlib.sha256(blob).hexdigest()})
            gold.append({"sample_id": sid, "answer": "1200", "exe_ans": 1200})
        (cls.tmp / "datasets" / "demo" / "runtime.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in runtime), encoding="utf-8")
        (cls.tmp / "datasets" / "demo" / "eval.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in gold), encoding="utf-8")
        (cls.tmp / "datasets" / "demo" / "pure_budget.json").write_text(
            json.dumps({"D0": 500, "D1": 500}), encoding="utf-8")

        os.environ["EVID_API_KEY"] = "test-key"
        os.environ["EVID_BASE_URL"] = "http://127.0.0.1:%d/v1" % cls.server.server_port
        from repro import common as C
        from repro import config
        from repro import runner as X
        cls.C, cls.X, cls.config = C, X, config
        cls.saved = (C.ROOT, C.DATA, C.RUNS, C.REPORTS, X.ROOT, config.MODEL,
                     X.source_files)
        C.ROOT, C.DATA, C.RUNS, C.REPORTS = cls.tmp, cls.tmp / "datasets", cls.tmp / "runs", cls.tmp / "reports"
        X.ROOT = cls.tmp
        config.MODEL = "mock-vlm"
        X.source_files = lambda: []            # code hashes are covered by the freeze test in CI

    @classmethod
    def tearDownClass(cls):
        from repro import common as C
        from repro import config
        from repro import runner as X
        C.ROOT, C.DATA, C.RUNS, C.REPORTS, X.ROOT, config.MODEL, X.source_files = cls.saved
        cls.server.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _reset(self):
        for name in ("runs", "reports"):
            shutil.rmtree(self.tmp / name, ignore_errors=True)
            (self.tmp / name).mkdir()

    def test_pipeline_end_to_end(self):
        X = self.X
        self._reset()
        X.freeze("demo")
        self.assertEqual(X.verify("demo")["n"], 2)

        X.run("demo", "initial", "0/1")
        X.merge("demo", "initial", 1)
        X.initial_freeze("demo")

        X.run("demo", "e2e", "0/1", with_ssc=False)
        X.merge("demo", "e2e", 1)
        X.run("demo", "repair", "0/1", with_ssc=False)
        X.merge("demo", "repair", 1)

        from repro import evaluate
        evaluate.evaluate("demo")
        metrics = json.loads((self.tmp / "reports" / "demo" / "metrics.json").read_text())
        self.assertEqual(metrics["e2e"]["EVID"]["final_counts"], {"C": 2})
        self.assertEqual(metrics["e2e"]["DIRECT"]["final_counts"], {"C": 2})
        self.assertEqual(metrics["repair"]["EVID"]["r2"]["final_counts"], {"C": 2})
        self.assertEqual(metrics["common_initial"], {"C": 2})
        self.assertGreater(metrics["e2e"]["EVID"]["cost"]["requests"], 0)

    def test_cache_reuses_identical_prefixes(self):
        from repro import client as API
        X = self.X
        self._reset()
        X.freeze("demo")
        image = str(self.tmp / "datasets" / "demo" / "images" / "D0.png")
        quota = {"per_sample": {"X": 5}, "batch_total": 10}
        prompt = X.DIRECT + "\nQuestion: How many units were shipped in 2011?"
        first = API.Caller(str(self.tmp / "runs" / "cache"), quota, run_prefix="t", response_mode="text")
        _, meta1 = first.call("X", "D0", image, prompt, purpose="p", request_scope="scope")
        second = API.Caller(str(self.tmp / "runs" / "cache"), quota, run_prefix="t", response_mode="text")
        _, meta2 = second.call("X", "D0", image, prompt, purpose="p", request_scope="scope")
        self.assertFalse(meta1.get("reused"))
        self.assertTrue(meta2.get("reused"))
        ledger = [json.loads(line) for line in
                  (self.tmp / "runs" / "cache" / "t_calls.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        self.assertEqual(sum(1 for row in ledger if not row.get("reused")), 1)
        self.assertEqual(len(ledger), 2)

    def test_drift_is_detected(self):
        X = self.X
        self._reset()
        X.freeze("demo")
        extra = self.tmp / "datasets" / "demo" / "manifest.json"
        extra.write_text("{}", encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                X.verify("demo")
        finally:
            extra.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
