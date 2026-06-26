import os
from dotenv import load_dotenv

load_dotenv("/opt/ai-support-agent/.env", override=True)


def _int_env(name, default, min_value=None, max_value=None):
    raw = os.getenv(name, str(default)).strip()

    try:
        value = int(raw)
    except Exception:
        raise RuntimeError(f"{name} must be integer. Current value: {raw}")

    if min_value is not None and value < min_value:
        raise RuntimeError(f"{name} must be >= {min_value}")

    if max_value is not None and value > max_value:
        raise RuntimeError(f"{name} must be <= {max_value}")

    return value


def _float_env(name, default, min_value=None, max_value=None):
    raw = os.getenv(name, str(default)).strip()

    try:
        value = float(raw)
    except Exception:
        raise RuntimeError(f"{name} must be float. Current value: {raw}")

    if min_value is not None and value < min_value:
        raise RuntimeError(f"{name} must be >= {min_value}")

    if max_value is not None and value > max_value:
        raise RuntimeError(f"{name} must be <= {max_value}")

    return value


OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip().strip('"').strip("'")
OPENAI_REALTIME_MODEL = (os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime") or "gpt-realtime").strip()

ZAMMAD_URL = (os.getenv("ZAMMAD_URL", "http://127.0.0.1:8080") or "").rstrip("/")
ZAMMAD_TOKEN = (os.getenv("ZAMMAD_TOKEN") or "").strip()
DEFAULT_ZAMMAD_GROUP = os.getenv("DEFAULT_ZAMMAD_GROUP", "Service Desk")
ZAMMAD_TIMEOUT = _int_env("ZAMMAD_TIMEOUT", 8, min_value=3, max_value=30)

CSV_USERS_FILE = os.getenv("CSV_USERS_FILE", "/opt/ai-support-agent/data/users.csv")

MAX_CONCURRENT_CALLS = _int_env("MAX_CONCURRENT_CALLS", 10, min_value=1, max_value=50)
CALL_MAX_SECONDS = _int_env("CALL_MAX_SECONDS", 1800, min_value=60, max_value=7200)

VAD_THRESHOLD = _float_env("VAD_THRESHOLD", 0.75, min_value=0.1, max_value=1.0)
VAD_SILENCE_MS = _int_env("VAD_SILENCE_MS", 1700, min_value=500, max_value=3000)
VAD_IDLE_TIMEOUT_MS = _int_env("VAD_IDLE_TIMEOUT_MS", 30000, min_value=5000, max_value=30000)

DASHBOARD_SECRET = (os.getenv("DASHBOARD_SECRET") or "").strip()
DASHBOARD_COOKIE_SECURE = (os.getenv("DASHBOARD_COOKIE_SECURE", "false").lower() == "true")


def validate_bridge_config():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")

    if not OPENAI_API_KEY.startswith("sk-"):
        raise RuntimeError("OPENAI_API_KEY format looks invalid")

    if not ZAMMAD_URL:
        raise RuntimeError("ZAMMAD_URL is missing")

    if not ZAMMAD_TOKEN:
        raise RuntimeError("ZAMMAD_TOKEN is missing")

    if VAD_IDLE_TIMEOUT_MS > 30000:
        raise RuntimeError("VAD_IDLE_TIMEOUT_MS must be <= 30000")


def validate_dashboard_config():
    if not DASHBOARD_SECRET:
        raise RuntimeError("DASHBOARD_SECRET is missing in .env")

    if DASHBOARD_SECRET == "change-this-dashboard-secret":
        raise RuntimeError("DASHBOARD_SECRET is using unsafe default value")

    if len(DASHBOARD_SECRET) < 32:
        raise RuntimeError("DASHBOARD_SECRET must be at least 32 characters")