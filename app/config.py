import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = os.getenv("ENV_FILE", str(BASE_DIR / ".env"))
load_dotenv(env_path, override=True)


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


# OpenAI Realtime Configuration
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip().strip('"').strip("'")
OPENAI_REALTIME_MODEL = (os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime") or "gpt-realtime").strip()

# Ticketing Configuration (Frappe Helpdesk)
FRAPPE_URL = (os.getenv("FRAPPE_URL", "http://127.0.0.1:8000") or "").rstrip("/")
FRAPPE_API_KEY = (os.getenv("FRAPPE_API_KEY") or "").strip()
FRAPPE_API_SECRET = (os.getenv("FRAPPE_API_SECRET") or "").strip()
FRAPPE_TICKET_DOCTYPE = (os.getenv("FRAPPE_TICKET_DOCTYPE", "HD Ticket") or "HD Ticket").strip()
FRAPPE_DEFAULT_TEAM = os.getenv("FRAPPE_DEFAULT_TEAM", "IT Support")

# Paths & Storage
CSV_USERS_FILE = os.getenv("CSV_USERS_FILE", str(BASE_DIR / "data" / "users.csv"))
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "dashboard.db"))

# Database Configuration (PostgreSQL 16 Enterprise with SQLite fallback)
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
DB_POOL_MIN = _int_env("DB_POOL_MIN", 2, min_value=1, max_value=50)
DB_POOL_MAX = _int_env("DB_POOL_MAX", 20, min_value=2, max_value=100)

# Telephony & Escalation Queues
ASTERISK_QUEUE_STANDARD = os.getenv("ASTERISK_QUEUE_STANDARD", os.getenv("ASTERISK_AGENT_EXTENSION", "7001"))
ASTERISK_QUEUE_EXECUTIVE = os.getenv("ASTERISK_QUEUE_EXECUTIVE", "7002")
ASTERISK_QUEUE_EMERGENCY = os.getenv("ASTERISK_QUEUE_EMERGENCY", "7003")

# Asterisk ARI Configuration
ASTERISK_ARI_URL = os.getenv("ASTERISK_ARI_URL", "http://127.0.0.1:8088").rstrip("/")
ASTERISK_ARI_USER = os.getenv("ASTERISK_ARI_USER", "asterisk")
ASTERISK_ARI_PASSWORD = os.getenv("ASTERISK_ARI_PASSWORD", "asterisk")
ASTERISK_ARI_APP = os.getenv("ASTERISK_ARI_APP", "ai-support")

# Call Parameters
MAX_CONCURRENT_CALLS = _int_env("MAX_CONCURRENT_CALLS", 10, min_value=1, max_value=50)
CALL_MAX_SECONDS = _int_env("CALL_MAX_SECONDS", 1800, min_value=60, max_value=7200)

# Voice Activity Detection (VAD)
VAD_THRESHOLD = _float_env("VAD_THRESHOLD", 0.65, min_value=0.1, max_value=1.0)
VAD_SILENCE_MS = _int_env("VAD_SILENCE_MS", 750, min_value=300, max_value=3000)
VAD_IDLE_TIMEOUT_MS = _int_env("VAD_IDLE_TIMEOUT_MS", 30000, min_value=5000, max_value=30000)

# Dashboard
DASHBOARD_SECRET = (os.getenv("DASHBOARD_SECRET") or "").strip()
DASHBOARD_COOKIE_SECURE = (os.getenv("DASHBOARD_COOKIE_SECURE", "false").lower() == "true")


def validate_bridge_config():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing in .env")

    if not OPENAI_API_KEY.startswith("sk-"):
        raise RuntimeError("OPENAI_API_KEY format looks invalid")

    if not FRAPPE_URL:
        raise RuntimeError("FRAPPE_URL is missing in .env")

    if VAD_IDLE_TIMEOUT_MS > 30000:
        raise RuntimeError("VAD_IDLE_TIMEOUT_MS must be <= 30000")


def validate_dashboard_config():
    if not DASHBOARD_SECRET:
        raise RuntimeError("DASHBOARD_SECRET is missing in .env")

    if DASHBOARD_SECRET == "change-this-dashboard-secret":
        raise RuntimeError("DASHBOARD_SECRET is using unsafe default value")

    if len(DASHBOARD_SECRET) < 32:
        raise RuntimeError("DASHBOARD_SECRET must be at least 32 characters")