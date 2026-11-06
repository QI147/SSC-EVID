"""Offline check of the second-model adapter (DeepSeek / GLM).

Starts a stub endpoint, sends one request per provider through
``repro.client.Caller``, and asserts the request shape each vendor requires.
No key, no network, no spend.

    python scripts/dryrun_second_model.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CAPTURED = {}


def jpeg_bytes():
    return b"\xff\xd8\xff\xe0" + b"0" * 64 + b"\xff\xd9"


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        CAPTURED["body"] = body
        content = body["messages"][0]["content"]
        CAPTURED["image_url"] = next(
            (part["image_url"]["url"][:32] for part in content
             if part.get("type") == "image_url"), None)
        payload = {
            "model": body.get("model"),
            "choices": [{"message": {"content": '{"answer": "1200"}',
                                     "reasoning_content": "looked at row 2"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def run_case(port, label, env, expectations):
    for key, value in env.items():
        os.environ[key] = value
    for module in [m for m in sys.modules if m.startswith("repro")]:
        del sys.modules[module]

    from repro import client

    workdir = Path(tempfile.mkdtemp(prefix="dryrun_%s_" % label))
    image = workdir / "table.jpg"
    image.write_bytes(jpeg_bytes())

    caller = client.Caller(rec_dir=str(workdir), quota={"per_question": 4},
                           run_prefix=label, response_mode="json")
    parsed, _ = caller.call("EVID", "q1", str(image), "stub prompt", "dryrun")

    body = CAPTURED["body"]
    ledger = workdir / ("%s_calls.jsonl" % label)
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0]) if ledger.exists() else {}

    checks = [
        ("模型名正确", body.get("model") == expectations["model"]),
        ("JPEG 按 image/jpeg 声明", (CAPTURED["image_url"] or "").startswith("data:image/jpeg")),
        ("JSON 可解析", parsed == {"answer": "1200"}),
        ("reasoning_content 已记录", record.get("reasoning_content") == "looked at row 2"),
    ]
    if expectations.get("dashscope_thinking"):
        checks.append(("保留 DashScope 的 enable_thinking（Qwen 需要）",
                       body.get("enable_thinking") is False))
    else:
        checks.append(("不含 DashScope 的 enable_thinking", "enable_thinking" not in body))
    if "thinking" in expectations:
        checks.append(("thinking=%s" % expectations["thinking"],
                       body.get("thinking") == {"type": expectations["thinking"]}))
    else:
        checks.append(("不发 thinking 块", "thinking" not in body))

    ok = True
    print("\n[%s] %s" % (label, expectations["model"]))
    for text, passed in checks:
        print(("  [PASS] " if passed else "  [FAIL] ") + text)
        ok = ok and passed
    return ok


def main():
    server = HTTPServer(("127.0.0.1", 0), Stub)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    base = {"EVID_BASE_URL": "http://127.0.0.1:%d" % port, "EVID_API_KEY": "stub-key"}
    results = []

    results.append(run_case(port, "deepseek", dict(base, **{
        "EVID_MODEL": "deepseek-flash",
        "EVID_SEND_THINKING": "0",
        "EVID_THINKING_BLOCK": "disabled",
    }), {"model": "deepseek-flash", "thinking": "disabled"}))

    results.append(run_case(port, "glm", dict(base, **{
        "EVID_MODEL": "glm-4.6v",
        "EVID_SEND_THINKING": "0",
        "EVID_THINKING_BLOCK": "",
    }), {"model": "glm-4.6v"}))

    results.append(run_case(port, "qwen", dict(base, **{
        "EVID_MODEL": "qwen3.8-flash",
        "EVID_SEND_THINKING": "1",
        "EVID_ENABLE_THINKING": "0",
        "EVID_THINKING_BLOCK": "",
    }), {"model": "qwen3.8-flash", "dashscope_thinking": True}))

    server.shutdown()
    ok = all(results)
    print("\n结果:", "三个供应商的请求形状都正确" if ok else "有失败项，先修再跑")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
