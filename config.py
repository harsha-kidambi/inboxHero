# Roll No: evernorth-aai-1164386
"""
config.py - every environment-dependent setting lives here (same convention as A4/A5).

The model is optional at runtime: if no provider/key is configured, or the provider
is unreachable, inboxHero falls back to its offline analyst so every command in the
manifest still runs on a clean checkout.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

# --- Model (any OpenAI-compatible chat-completions endpoint) -------------------------
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "offline").lower()   # nvidia | gemini | ollama | offline
API_KEY_VAR = f"{MODEL_PROVIDER.upper()}_API_KEY"
API_KEY = os.getenv(API_KEY_VAR, "ollama" if MODEL_PROVIDER == "ollama" else "")
MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", "https://integrate.api.nvidia.com/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "nvidia/nemotron-3-super-120b-a12b")
MODEL_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.2"))
MODEL_TIMEOUT = float(os.getenv("MODEL_TIMEOUT", "120"))
MODEL_MAX_TOKENS = int(os.getenv("MODEL_MAX_TOKENS", "2048"))

# Free tiers allow ~15-40 requests/minute. Space calls out and back off on HTTP 429.
LLM_MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL", "4"))      # seconds between calls
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "4"))
TRIAGE_BATCH_SIZE = int(os.getenv("TRIAGE_BATCH_SIZE", "8"))       # messages per triage call

# --- The mailbox owner ------------------------------------------------------------------
OWNER = os.getenv("OWNER_EMAIL", "sam@paperjet.io")
OWNER_NAME = os.getenv("OWNER_NAME", "Sam")
ORG_DOMAIN = OWNER.split("@", 1)[1]

# --- Files --------------------------------------------------------------------------------
INBOX_FILE = BASE_DIR / os.getenv("INBOX_FILE", "inbox.json")
PREFS_FILE = BASE_DIR / "prefs.json"
TRACE_FILE = BASE_DIR / "trace.jsonl"
GATE_LOG = BASE_DIR / "gate_log.jsonl"
LLM_CACHE_FILE = BASE_DIR / "llm_cache.json"
OUTBOX_DIR = BASE_DIR / "outbox"
RUN_DIR = BASE_DIR / "run"          # decisions.json, pending.json, flags.json, commitments.json
DASHBOARD_HTML = BASE_DIR / "dashboard.html"
DASHBOARD_JSON = BASE_DIR / "dashboard.json"


def model_enabled() -> bool:
    return MODEL_PROVIDER != "offline" and bool(API_KEY)


def describe_model() -> str:
    if not model_enabled():
        return "offline analyst (no model configured)"
    return f"{MODEL_PROVIDER} / {MODEL_NAME}"
