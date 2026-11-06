"""Run-time configuration. Every value can be overridden by an environment variable.

The harness talks to any OpenAI-compatible ``/chat/completions`` endpoint, so the
paper's provider is not hard-wired. Credentials are read from the environment or
from a key=value file outside the repository (``EVID_CRED_FILE``); no key is ever
written into the tree.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------- model
MODEL = os.environ.get("EVID_MODEL", "qwen3.8-flash")
TEMPERATURE = 0
# ``enable_thinking`` is a DashScope-specific field. Endpoints that do not
# implement it reject the whole request, so it is only sent when explicitly
# allowed (default: send, preserving the original behaviour).
ENABLE_THINKING = os.environ.get("EVID_ENABLE_THINKING", "0") == "1"
SEND_THINKING_FLAG = os.environ.get("EVID_SEND_THINKING", "1") == "1"
# Provider-neutral form: {"thinking": {"type": "<value>"}}. DeepSeek enables
# chain-of-thought by default and must be told "disabled" explicitly, otherwise
# the reasoning trace eats the output budget and ``content`` comes back empty.
THINKING_BLOCK = os.environ.get("EVID_THINKING_BLOCK", "").strip().lower()
MAX_TOKENS = int(os.environ.get("EVID_MAX_TOKENS", "1200"))
RETRY = 0                      # the paper does not retry to success

# ---------------------------------------------------------------- endpoint
BASE_URL = (os.environ.get("EVID_BASE_URL")
            or os.environ.get("BAILIAN_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1")
API_KEY_ENV = "EVID_API_KEY"
FALLBACK_KEY_ENV = "BAILIAN_API_KEY"

# When set, a request is only sent if the endpoint host matches this value.
# Empty means "no host restriction"; the paper's runs pinned one host.
ALLOWED_HOST = os.environ.get("EVID_ALLOWED_HOST", "").strip()

# ---------------------------------------------------------------- price (CNY / 1M tokens)
# Frozen planning prices used for the paper's cost table. They are estimates,
# not a provider invoice.
PRICE_INPUT = float(os.environ.get("EVID_PRICE_INPUT", "0.8"))
PRICE_OUTPUT = float(os.environ.get("EVID_PRICE_OUTPUT", "2.7"))

# ---------------------------------------------------------------- limits
# Max real attempts for one logical request (transport failures only).
# A gateway outage is retried more times than the default without ever being
# charged to the method's per-item budget (see client.Caller._limit_hit).
TRANSPORT_ATTEMPTS = int(os.environ.get("EVID_TRANSPORT_ATTEMPTS", "3"))
# Restrict the end-to-end stage to a subset of arms, e.g. "ssc,evid" for the
# cross-model replication.  Empty means all four arms (the paper's protocol).
E2E_ARMS = {a.strip().lower() for a in os.environ.get("EVID_E2E_ARMS", "").split(",") if a.strip()}
# Max independent re-reads per question in the budget-matched control.
MAX_READS = 16

ROOT = Path(__file__).resolve().parent.parent


def load_credentials():
    """Return {'base_url', 'api_key'} from the environment or a credentials file."""
    key = os.environ.get(API_KEY_ENV) or os.environ.get(FALLBACK_KEY_ENV)
    url = os.environ.get("EVID_BASE_URL") or os.environ.get("BAILIAN_BASE_URL")
    if key and url:
        return {"base_url": url.rstrip("/"), "api_key": key}
    path = os.environ.get("EVID_CRED_FILE") or os.environ.get("BAILIAN_CRED_FILE")
    if not path or not os.path.exists(path):
        raise SystemExit(
            "No credentials. Set EVID_API_KEY and EVID_BASE_URL, or point "
            "EVID_CRED_FILE at a key=value file containing BAILIAN_BASE_URL and "
            "BAILIAN_API_KEY (see .env.example).")
    values = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip('"').strip("'")
    url = url or values.get("BAILIAN_BASE_URL") or values.get("EVID_BASE_URL")
    key = key or values.get("BAILIAN_API_KEY") or values.get("EVID_API_KEY")
    if not url or not key:
        raise SystemExit("Credentials file %s is missing a base URL or an API key." % path)
    return {"base_url": url.rstrip("/"), "api_key": key}
