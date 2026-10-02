import os
import asyncio
import csv
import io
import re
import hmac
import time
import sqlite3
import hashlib
import secrets
import socket
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, HTTPException, Response, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import (
    validate_dashboard_config, DASHBOARD_SECRET, DASHBOARD_COOKIE_SECURE,
    DASHBOARD_HOST, DASHBOARD_PORT, INITIAL_ADMIN_PASSWORD
)
from app.security_guard import check_rate_limit, log_security_event, reset_rate_limit, init_security_db
import app.db as app_db

validate_dashboard_config()

BASE_DIR = Path(__file__).resolve().parent.parent
APP_SECRET = DASHBOARD_SECRET
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "dashboard.db"))
RECORDING_DIR = os.getenv("RECORDING_DIR", "/var/spool/asterisk/monitor/ai-support")
BRAND_ASSETS_DIR = os.getenv("BRAND_ASSETS_DIR", str(BASE_DIR / "brand-assets"))
STATIC_DIR = os.getenv("STATIC_DIR", str(BASE_DIR / "dashboard" / "static"))
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", str(BASE_DIR / "dashboard" / "templates"))

NF_LOGO_LOCAL = "/brand-assets/nfc-logo.svg"
TCT_LOGO_LOCAL = "/brand-assets/tct-logo.png"
COOKIE_NAME = "ai_dashboard_token"
CSRF_COOKIE_NAME = "csrf_token"
SESSION_TTL_SECONDS = 28800

app = FastAPI(title="National Finance AI IT Support Operations")

# Mount Static Assets & Templates
Path(BRAND_ASSETS_DIR).mkdir(parents=True, exist_ok=True)
Path(STATIC_DIR).mkdir(parents=True, exist_ok=True)
Path(TEMPLATES_DIR).mkdir(parents=True, exist_ok=True)

app.mount("/brand-assets", StaticFiles(directory=BRAND_ASSETS_DIR), name="brand-assets")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# -----------------------------------------------------------------------------
# Enterprise Security Headers Middleware
# -----------------------------------------------------------------------------

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: /brand-assets/; "
        "media-src 'self'; "
        "connect-src 'self';"
    )
    return response


# -----------------------------------------------------------------------------
# Global Route Rate Limiting Middleware
# -----------------------------------------------------------------------------

DASHBOARD_RATE_LIMIT = int(os.getenv("DASHBOARD_RATE_LIMIT", "120"))
DASHBOARD_RATE_WINDOW = int(os.getenv("DASHBOARD_RATE_WINDOW", "60"))
_RATE_LIMIT_STORE = {}
_RATE_LIMIT_LOCK = asyncio.Lock()


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static") or path.startswith("/brand-assets"):
        return await call_next(request)

    client_ip = request.client.host if request.client else "127.0.0.1"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    now = time.time()
    async with _RATE_LIMIT_LOCK:
        timestamps = _RATE_LIMIT_STORE.get(client_ip, [])
        timestamps = [t for t in timestamps if now - t < DASHBOARD_RATE_WINDOW]
        if len(timestamps) >= DASHBOARD_RATE_LIMIT:
            return JSONResponse(
                {"error": "Too many requests. Rate limit exceeded. Please try again later."},
                status_code=429,
                headers={"Retry-After": str(DASHBOARD_RATE_WINDOW)},
            )
        timestamps.append(now)
        _RATE_LIMIT_STORE[client_ip] = timestamps

    return await call_next(request)


# -----------------------------------------------------------------------------
# Database helpers
# -----------------------------------------------------------------------------

def db():
    return app_db.get_db()


def _ensure_column(conn, table, column, definition):
    app_db._ensure_column(conn, table, column, definition)


# -----------------------------------------------------------------------------
# Password and session helpers
# -----------------------------------------------------------------------------

def hash_password(password, salt=None):
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return f"{salt}${hashed}"


def verify_password(password, stored_hash):
    if not stored_hash:
        return False
    if "$" not in stored_hash:
        # Legacy static salt fallback
        legacy = hashlib.pbkdf2_hmac("sha256", password.encode(), b"ai-support-agent", 100000).hex()
        return hmac.compare_digest(legacy, stored_hash)
    try:
        salt, expected = stored_hash.split("$", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_session(username, ttl_seconds=SESSION_TTL_SECONDS):
    token = secrets.token_urlsafe(48)
    now = int(time.time())
    expires_at = now + ttl_seconds

    conn = db()
    conn.execute(
        """
        INSERT INTO sessions(token, username, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (token, username, now, expires_at),
    )
    conn.commit()
    conn.close()
    return token


def get_session_user(token):
    if not token:
        return None

    now = int(time.time())
    conn = db()
    row = conn.execute(
        """
        SELECT username FROM sessions
        WHERE token=? AND expires_at > ?
        """,
        (token, now),
    ).fetchone()
    conn.close()
    return row["username"] if row else None


def delete_session(token):
    if not token:
        return

    conn = db()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()


def invalidate_user_sessions(username):
    """Terminates all active sessions for a user (e.g. on password change/reset)."""
    if not username:
        return
    conn = db()
    conn.execute("DELETE FROM sessions WHERE username=?", (username,))
    conn.commit()
    conn.close()


def cleanup_expired_sessions():
    now = int(time.time())
    conn = db()
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
    conn.commit()
    conn.close()


# -----------------------------------------------------------------------------
# CSRF helpers
# -----------------------------------------------------------------------------

def ensure_csrf_token(request: Request):
    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = secrets.token_urlsafe(32)
    return token


def validate_csrf(request: Request, submitted_token):
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token or not submitted_token:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    if not hmac.compare_digest(cookie_token, submitted_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def is_request_secure(request: Request) -> bool:
    if not DASHBOARD_COOKIE_SECURE:
        return False
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


# -----------------------------------------------------------------------------
# Auth & First-Run Setup helpers
# -----------------------------------------------------------------------------

def has_admin_user() -> bool:
    try:
        conn = db()
        row = conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin' AND active=1").fetchone()
        conn.close()
        return (row["c"] if row else 0) > 0
    except Exception:
        return False


def current_user(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    username = get_session_user(token)
    if not username:
        return None

    conn = db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=? AND active=1",
        (username,),
    ).fetchone()
    conn.close()
    return user


def require_user(request: Request):
    if not has_admin_user():
        raise HTTPException(status_code=302, headers={"Location": "/setup"})
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def require_roles(request: Request, allowed_roles):
    user = require_user(request)
    if user["role"] not in allowed_roles:
        raise HTTPException(status_code=403, detail="Access denied")
    return user


# -----------------------------------------------------------------------------
# Audit and settings
# -----------------------------------------------------------------------------

def audit(username, action, entity_type="", entity_id=""):
    conn = db()
    created_at = int(time.time())
    try:
        last_row = conn.execute(
            "SELECT record_hash FROM audit_logs WHERE record_hash IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = last_row["record_hash"] if last_row and last_row["record_hash"] else "GENESIS"
    except Exception:
        prev_hash = "GENESIS"

    raw_payload = f"{prev_hash}|{username}|{action}|{entity_type}|{entity_id}|{created_at}".encode("utf-8")
    record_hash = hashlib.sha256(raw_payload).hexdigest()

    conn.execute(
        """
        INSERT INTO audit_logs(username, action, entity_type, entity_id, created_at, prev_hash, record_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (username, action, entity_type, entity_id, created_at, prev_hash, record_hash),
    )
    conn.commit()
    conn.close()


def verify_audit_trail() -> dict:
    conn = db()
    rows = conn.execute(
        "SELECT id, username, action, entity_type, entity_id, created_at, prev_hash, record_hash FROM audit_logs ORDER BY id ASC"
    ).fetchall()
    conn.close()

    expected_prev = "GENESIS"
    verified_count = 0
    for r in rows:
        prev_h = r["prev_hash"]
        rec_h = r["record_hash"]
        if not rec_h:
            continue
        if prev_h != expected_prev:
            return {"valid": False, "compromised_id": r["id"], "reason": f"broken chain at id {r['id']}"}
        payload = f"{prev_h}|{r['username']}|{r['action']}|{r['entity_type'] or ''}|{r['entity_id'] or ''}|{r['created_at']}".encode("utf-8")
        calc_hash = hashlib.sha256(payload).hexdigest()
        if rec_h != calc_hash:
            return {"valid": False, "compromised_id": r["id"], "reason": f"tampered record hash at id {r['id']}"}
        expected_prev = rec_h
        verified_count += 1

    return {"valid": True, "verified_records": verified_count}


def get_setting(key, default=""):
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row and row["value"] is not None:
        return row["value"]
    return default


def save_setting(conn, key, value):
    conn.execute(
        """
        INSERT INTO settings(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, int(time.time())),
    )


# -----------------------------------------------------------------------------
# Retention & Telemetry formatting helpers
# -----------------------------------------------------------------------------

def enforce_recording_retention():
    """Prunes recording files older than recording_retention_days."""
    try:
        days_str = get_setting("recording_retention_days", "30")
        days = int(days_str) if days_str.isdigit() else 30
        if days <= 0:
            return 0
        cutoff = time.time() - (days * 86400)
        base = Path(RECORDING_DIR)
        if not base.exists():
            return 0

        deleted_count = 0
        for f in base.glob("*.wav"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted_count += 1
            except Exception:
                pass

        if deleted_count > 0:
            audit("system", "retention_prune", "recording", f"pruned {deleted_count} files older than {days} days")
        return deleted_count
    except Exception:
        return 0


def human_time(ts):
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return ""


STATUS_HUMAN_MAP = {
    "language_selected": "Language Chosen",
    "in_progress": "In Progress",
    "verified": "Caller Verified",
    "troubleshooting": "Troubleshooting",
    "ai_deflected": "AI Deflected",
    "AI_Resolved": "AI Deflected",
    "transferred": "Transferred to Agent",
    "emergency_escalated": "Sev-1 Escalation",
    "ended": "Completed",
    "completed": "Completed",
    "failed_verification": "Verification Failed",
    "failed": "Failed",
}


def human_status(status: str) -> str:
    if not status:
        return "In Progress"
    if status in STATUS_HUMAN_MAP:
        return STATUS_HUMAN_MAP[status]
    return status.replace("_", " ").replace("-", " ").title()


# Register custom filters into Jinja template environment
templates.env.filters["human_time"] = human_time
templates.env.filters["human_status"] = human_status


def extract_caller_from_recording(recording_file):
    if not recording_file:
        return ""
    name = Path(recording_file).name
    stem = Path(recording_file).stem
    if "_" in stem:
        parts = stem.split("_")
        if len(parts) >= 3:
            return parts[2]
    parts = stem.split("-")
    if len(parts) >= 4:
        return parts[-2]
    return ""


def display_caller_id(row):
    caller = row["caller_number"] or ""
    if caller:
        return caller
    return extract_caller_from_recording(row["recording_file"] or "")


def display_caller_name(row):
    if row["verified_name"]:
        return row["verified_name"]
    caller_id = display_caller_id(row)
    if caller_id:
        return f"Unknown - {caller_id}"
    return "Unknown"


def format_minutes(seconds):
    if seconds is None or seconds == "":
        return ""
    try:
        return f"{int(seconds) / 60:.1f}"
    except Exception:
        return ""


def live_call_duration(start_time):
    if not start_time:
        return "0.0"
    try:
        return f"{(int(time.time()) - int(start_time)) / 60:.1f}"
    except Exception:
        return "0.0"


def recording_files():
    base = Path(RECORDING_DIR)
    if not base.exists():
        return []

    files = []
    for file in sorted(base.glob("*.wav"), reverse=True):
        stat = file.stat()
        caller = "Unknown"
        stem = file.stem
        if "_" in stem:
            parts = stem.split("_")
            if len(parts) >= 3:
                caller = parts[2]
        else:
            parts = stem.split("-")
            if len(parts) >= 4:
                caller = parts[-2]

        files.append(
            {
                "name": file.name,
                "name_encoded": quote(file.name),
                "path": str(file),
                "caller": caller,
                "size_mb": round(stat.st_size / 1024 / 1024, 2),
                "created_at": stat.st_mtime,
                "created_at_human": human_time(stat.st_mtime),
            }
        )
    return files


def check_tcp_port(host, port, timeout=2):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def check_systemd_service(service_name):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def check_url(url, timeout=3):
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status < 500
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Template Rendering Helper
# -----------------------------------------------------------------------------

def render_template(request: Request, template_name: str, context: dict = None, status_code: int = 200):
    if context is None:
        context = {}

    csrf_token = ensure_csrf_token(request)
    context["request"] = request
    context["csrf_token"] = csrf_token
    context["nf_logo_url"] = NF_LOGO_LOCAL
    context["tct_logo_url"] = TCT_LOGO_LOCAL
    context["organization_name"] = get_setting("organization_name", "National Finance Oman")
    context["dashboard_title"] = get_setting("dashboard_title", "AI IT Support Operations")
    context["dashboard_subtitle"] = get_setting("dashboard_subtitle", "Voice AI Support Telemetry")

    if "user" not in context:
        context["user"] = current_user(request)

    response = templates.TemplateResponse(request, template_name, context, status_code=status_code)
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        httponly=True,
        secure=is_request_secure(request),
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )
    return response


# -----------------------------------------------------------------------------
# Database Initialization (Zero Default Passwords)
# -----------------------------------------------------------------------------

def init_db():
    app_db.init_all_tables()
    conn = db()

    # Zero hardcoded default passwords.
    # Check if INITIAL_ADMIN_PASSWORD is provided via environment (optional bootstrap)
    if INITIAL_ADMIN_PASSWORD and not has_admin_user():
        conn.execute(
            """
            INSERT INTO users(username, password_hash, role, active, created_at)
            VALUES (?, ?, 'admin', 1, ?)
            """,
            ("admin", hash_password(INITIAL_ADMIN_PASSWORD), int(time.time())),
        )

    default_settings = {
        "organization_name": "National Finance Oman",
        "dashboard_title": "AI IT Support Operations",
        "dashboard_subtitle": "Voice AI Support Telemetry",
        "ai_greeting": "Hi, I am Arif from National Finance IT Support team. Please say Arabic or English to continue.",
        "system_prompt": "You are Arif, an AI IT Support voice agent for National Finance IT Support team.",
        "max_concurrent_calls": "10",
        "recording_retention_days": "30",
        "frappe_enabled": "true",
        "profile_icon_text": "NF",
    }

    for key, value in default_settings.items():
        exists = conn.execute("SELECT key FROM settings WHERE key=?", (key,)).fetchone()
        if not exists:
            save_setting(conn, key, value)

    conn.commit()
    conn.close()
    init_security_db()


@app.on_event("startup")
def startup():
    init_db()
    cleanup_expired_sessions()
    enforce_recording_retention()
    try:
        from app.ad_sync import start_ad_sync_worker
        start_ad_sync_worker()
    except Exception:
        pass


# -----------------------------------------------------------------------------
# First-Run Setup Wizard Routes
# -----------------------------------------------------------------------------

@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if has_admin_user():
        return RedirectResponse("/login", status_code=302)
    return render_template(request, "setup.html", {"title": "Administrator Setup"})


@app.post("/setup", response_class=HTMLResponse)
def setup_admin(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    client_ip = request.client.host if request.client else "127.0.0.1"

    if has_admin_user():
        raise HTTPException(status_code=403, detail="Administrator setup is already completed and locked.")

    if not check_rate_limit(f"setup_admin:{client_ip}", max_attempts=5, window_seconds=600):
        return render_template(
            request, "setup.html",
            {"error": "Too many setup attempts. Please wait 10 minutes.", "title": "Administrator Setup"},
            status_code=429
        )

    username = username.strip()
    if len(username) < 3 or not re.match(r"^[a-zA-Z0-9_.-]+$", username):
        return render_template(
            request, "setup.html",
            {"error": "Username must be at least 3 characters and alphanumeric.", "title": "Administrator Setup"},
            status_code=400
        )

    if password != confirm_password:
        return render_template(
            request, "setup.html",
            {"error": "Passwords do not match.", "title": "Administrator Setup"},
            status_code=400
        )

    if len(password) < 10:
        return render_template(
            request, "setup.html",
            {"error": "Password must be at least 10 characters long.", "title": "Administrator Setup"},
            status_code=400
        )

    if not any(c.isdigit() or not c.isalnum() for c in password):
        return render_template(
            request, "setup.html",
            {"error": "Password must contain at least one number or special symbol.", "title": "Administrator Setup"},
            status_code=400
        )

    conn = db()
    conn.execute(
        """
        INSERT INTO users(username, password_hash, role, active, created_at)
        VALUES (?, ?, 'admin', 1, ?)
        """,
        (username, hash_password(password), int(time.time())),
    )
    conn.commit()
    conn.close()

    audit(username, "bootstrap_initial_admin", "user", username)
    log_security_event("initial_admin_setup_success", client_ip, f"username={username}")

    token = create_session(username)
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=is_request_secure(request),
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )
    return response


# -----------------------------------------------------------------------------
# Auth routes
# -----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not has_admin_user():
        return RedirectResponse("/setup", status_code=302)
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user["role"] == "quality_reviewer":
        return RedirectResponse("/recordings", status_code=302)
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not has_admin_user():
        return RedirectResponse("/setup", status_code=302)
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=302)
    return render_template(request, "login.html", {"title": "Login"})


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
):
    try:
        validate_csrf(request, csrf_token)
    except HTTPException:
        return render_template(
            request, "login.html",
            {"error": "Session security token expired. Please refresh the page and sign in again.", "title": "Login"},
            status_code=403
        )
    client_ip = request.client.host if request.client else "127.0.0.1"

    if not check_rate_limit(f"dashboard_login:{client_ip}", max_attempts=5, window_seconds=300):
        log_security_event("dashboard_login_rate_limited", client_ip, f"username={username}")
        return render_template(
            request, "login.html",
            {"error": "Too many failed login attempts. Please wait 5 minutes.", "title": "Login"},
            status_code=429
        )

    conn = db()
    user = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    conn.close()

    if not user or not verify_password(password, user["password_hash"]):
        log_security_event("dashboard_login_failed", client_ip, f"username={username}")
        return render_template(
            request, "login.html",
            {"error": "Invalid username or password. Please try again.", "title": "Login"},
            status_code=401
        )

    # Upgrade legacy static salt to modern random salt upon successful login
    if "$" not in user["password_hash"]:
        c = db()
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user["id"]))
        c.commit()
        c.close()

    cleanup_expired_sessions()
    reset_rate_limit(f"dashboard_login:{client_ip}")
    log_security_event("dashboard_login_success", client_ip, f"username={username}")

    token = create_session(username)
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=is_request_secure(request),
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )
    return response


@app.get("/logout")
def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    delete_session(token)
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(CSRF_COOKIE_NAME)
    return response


# -----------------------------------------------------------------------------
# REST API Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/dashboard/stats")
def api_dashboard_stats(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    live_cutoff = int(time.time()) - 600
    conn = db()
    total_calls = conn.execute("SELECT COUNT(*) c FROM calls").fetchone()["c"]
    tickets = conn.execute("SELECT COUNT(*) c FROM calls WHERE ticket_created=1").fetchone()["c"]
    verified = conn.execute("SELECT COUNT(*) c FROM calls WHERE verified_name IS NOT NULL AND verified_name != ''").fetchone()["c"]
    failed = conn.execute(
        """
        SELECT COUNT(*) c FROM calls
        WHERE status IN ('failed','openai_connection_failed','openai_response_failed','ticket_failed','verification_failed','verification_blocked','transfer_failed')
        """
    ).fetchone()["c"]
    ongoing = conn.execute(
        """
        SELECT COUNT(*) c FROM calls
        WHERE end_time IS NULL
        AND start_time >= ?
        AND status IN ('in_progress','language_selected','verified','troubleshooting')
        """,
        (live_cutoff,),
    ).fetchone()["c"]
    vip_calls = conn.execute("SELECT COUNT(*) c FROM calls WHERE is_vip=1 OR tier IN ('P0_EXECUTIVE','P1_VIP')").fetchone()["c"]
    deflected = conn.execute("SELECT COUNT(*) c FROM calls WHERE ai_deflected=1 OR resolution_type='AI_Resolved'").fetchone()["c"]
    emergency_calls = conn.execute("SELECT COUNT(*) c FROM calls WHERE tier='CRITICAL' OR status='emergency_escalated'").fetchone()["c"]
    transferred = conn.execute("SELECT COUNT(*) c FROM calls WHERE transferred=1").fetchone()["c"]
    conn.close()

    deflection_rate = round((deflected / total_calls * 100), 1) if total_calls > 0 else 0.0

    return {
        "total_calls": total_calls,
        "deflected": deflected,
        "deflection_rate": deflection_rate,
        "ongoing": ongoing,
        "emergency_calls": emergency_calls,
        "vip_calls": vip_calls,
        "tickets": tickets,
        "verified": verified,
        "failed": failed,
        "transferred": transferred,
    }


@app.get("/api/dashboard/chart-data")
def api_dashboard_chart_data(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = int(time.time())
    one_day_ago = now - 86400

    conn = db()
    rows = conn.execute(
        """
        SELECT start_time, status, ai_deflected, resolution_type, tier, transferred, ticket_created
        FROM calls
        WHERE start_time >= ?
        ORDER BY start_time ASC
        """,
        (one_day_ago,),
    ).fetchall()

    deflected_count = conn.execute("SELECT COUNT(*) c FROM calls WHERE ai_deflected=1 OR resolution_type='AI_Resolved'").fetchone()["c"]
    transferred_l1 = conn.execute("SELECT COUNT(*) c FROM calls WHERE transferred=1 AND tier NOT IN ('P0_EXECUTIVE','P1_VIP','CRITICAL')").fetchone()["c"]
    transferred_vip = conn.execute("SELECT COUNT(*) c FROM calls WHERE transferred=1 AND tier IN ('P0_EXECUTIVE','P1_VIP')").fetchone()["c"]
    transferred_emerg = conn.execute("SELECT COUNT(*) c FROM calls WHERE tier='CRITICAL' OR status='emergency_escalated'").fetchone()["c"]
    tickets_count = conn.execute("SELECT COUNT(*) c FROM calls WHERE ticket_created=1").fetchone()["c"]
    conn.close()

    labels = []
    bucket_total = [0] * 12
    bucket_deflected = [0] * 12
    bucket_escalated = [0] * 12

    for i in range(12):
        bucket_time = now - (11 - i) * 7200
        labels.append(time.strftime("%H:00", time.localtime(bucket_time)))

    for r in rows:
        st = r["start_time"]
        if not st:
            continue
        diff = now - int(st)
        if 0 <= diff <= 86400:
            idx = 11 - min(11, int(diff // 7200))
            bucket_total[idx] += 1
            if r["ai_deflected"] == 1 or r["resolution_type"] == "AI_Resolved":
                bucket_deflected[idx] += 1
            elif r["transferred"] == 1 or r["tier"] == "CRITICAL" or r["status"] == "emergency_escalated":
                bucket_escalated[idx] += 1

    return {
        "volume_trend": {
            "labels": labels,
            "total": bucket_total,
            "deflected": bucket_deflected,
            "escalated": bucket_escalated,
        },
        "deflection_breakdown": [
            deflected_count,
            transferred_l1,
            transferred_vip,
            transferred_emerg,
            tickets_count,
        ],
        "queue_distribution": [
            transferred_l1,
            transferred_vip,
            transferred_emerg,
        ]
    }


@app.get("/api/active-calls/data")
def api_active_calls_data(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    live_cutoff = int(time.time()) - 1800
    conn = db()
    rows = conn.execute(
        """
        SELECT * FROM calls
        WHERE end_time IS NULL
        AND start_time >= ?
        AND status IN ('in_progress','language_selected','verified','troubleshooting')
        ORDER BY start_time ASC
        """,
        (live_cutoff,),
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        caller_disp = display_caller_id(r)
        name_disp = display_caller_name(r)
        result.append({
            "id": r["id"],
            "call_id": r["call_id"] or "",
            "caller_id": caller_disp,
            "caller_number": r["caller_number"] or caller_disp,
            "caller_name": name_disp,
            "verified_name": r["verified_name"] or name_disp,
            "employee_id": r["employee_id"] or "",
            "tier": r["tier"] or "STANDARD",
            "status": human_status(r["status"] or "in_progress"),
            "raw_status": r["status"] or "in_progress",
            "duration_minutes": live_call_duration(r["start_time"]),
            "started_at": human_time(r["start_time"]),
        })
    return result


@app.get("/api/calls/{call_identifier}")
def api_call_detail(request: Request, call_identifier: str):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    conn = db()
    if call_identifier.isdigit():
        row = conn.execute(
            "SELECT * FROM calls WHERE id=? OR call_id=?",
            (int(call_identifier), call_identifier),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM calls WHERE call_id=?",
            (call_identifier,),
        ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Call session not found")

    return {
        "id": row["id"],
        "call_id": row["call_id"] or "",
        "caller_number": display_caller_id(row),
        "verified_name": row["verified_name"] or "",
        "employee_id": row["employee_id"] or "",
        "tier": row["tier"] or "STANDARD",
        "language": row["language"] or "en",
        "duration_minutes": format_minutes(row["duration_seconds"]),
        "status": human_status(row["status"] or "completed"),
        "raw_status": row["status"] or "completed",
        "ticket_number": row["ticket_number"] or "",
        "summary": row["summary"] or "",
        "transcript": row.get("transcript") or "",
        "recording_file": row["recording_file"] or "",
        "started_at": human_time(row["start_time"]),
        "ended_at": human_time(row["end_time"]),
    }


# -----------------------------------------------------------------------------
# Main Dashboard Pages
# -----------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_roles(request, ["admin", "user"])
    stats_data = api_dashboard_stats(request)

    conn = db()
    recent_calls = conn.execute("SELECT * FROM calls ORDER BY id DESC LIMIT 10").fetchall()
    conn.close()

    return render_template(
        request, "dashboard.html",
        {
            "title": "Overview",
            "active_page": "dashboard",
            "user": user,
            "stats": stats_data,
            "recent_calls": recent_calls,
        }
    )


@app.get("/calls", response_class=HTMLResponse)
def calls(request: Request):
    user = require_roles(request, ["admin", "user"])

    q = request.query_params.get("q", "").strip()
    employee_id = request.query_params.get("employee_id", "").strip()
    status = request.query_params.get("status", "").strip()
    language = request.query_params.get("language", "").strip()
    ticket_created = request.query_params.get("ticket_created", "").strip()
    export = request.query_params.get("export", "").strip()

    sql = "SELECT * FROM calls WHERE 1=1"
    params = []

    if q:
        sql += """
        AND (
            caller_number LIKE ? OR verified_name LIKE ? OR employee_id LIKE ?
            OR ticket_number LIKE ? OR status LIKE ? OR summary LIKE ?
        )
        """
        like = f"%{q}%"
        params.extend([like, like, like, like, like, like])

    if employee_id:
        sql += " AND employee_id LIKE ?"
        params.append(f"%{employee_id}%")
    if status:
        sql += " AND status=?"
        params.append(status)
    if language:
        sql += " AND language=?"
        params.append(language)
    if ticket_created in ["0", "1"]:
        sql += " AND ticket_created=?"
        params.append(int(ticket_created))

    sql += " ORDER BY id DESC LIMIT 500"

    conn = db()
    rows = conn.execute(sql, params).fetchall()
    status_rows = conn.execute("SELECT DISTINCT status FROM calls WHERE status IS NOT NULL AND status != '' ORDER BY status").fetchall()
    conn.close()

    # Handle CSV Export
    if export == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Call ID", "Caller Number", "Employee ID", "Verified Name", "Tier", "Language", "Duration Sec", "Status", "Ticket Number", "Summary", "Start Time"])
        for r in rows:
            writer.writerow([
                r["id"], r["call_id"], r["caller_number"], r["employee_id"], r["verified_name"],
                r["tier"], r["language"], r["duration_seconds"], r["status"], r["ticket_number"],
                r["summary"], human_time(r["start_time"])
            ])
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=calls_export_{int(time.time())}.csv"}
        )

    return render_template(
        request, "calls.html",
        {
            "title": "Call History",
            "active_page": "calls",
            "user": user,
            "rows": rows,
            "status_rows": status_rows,
            "q": q,
            "status": status,
            "language": language,
            "ticket_created": ticket_created,
        }
    )


@app.get("/active-calls", response_class=HTMLResponse)
def active_calls_page(request: Request):
    user = require_roles(request, ["admin", "user"])
    active_calls = api_active_calls_data(request)

    return render_template(
        request, "active_calls.html",
        {
            "title": "Active Calls",
            "active_page": "active_calls",
            "user": user,
            "rows": active_calls,
        }
    )


@app.get("/failed-calls", response_class=HTMLResponse)
def failed_calls_page(request: Request):
    user = require_roles(request, ["admin", "user"])
    failed_statuses = [
        "failed", "openai_connection_failed", "openai_response_failed", "ticket_failed",
        "verification_failed", "verification_blocked", "audio_unclear", "transfer_failed",
        "rejected", "max_concurrent_rejected", "emergency_escalated",
    ]
    placeholders = ",".join(["?"] * len(failed_statuses))

    conn = db()
    raw_rows = conn.execute(
        f"SELECT * FROM calls WHERE status IN ({placeholders}) OR tier='CRITICAL' ORDER BY id DESC LIMIT 300",
        failed_statuses,
    ).fetchall()
    conn.close()

    formatted_rows = []
    for r in raw_rows:
        formatted_rows.append({
            "id": r["id"],
            "call_id": r["call_id"] or "",
            "caller_number": display_caller_id(r),
            "verified_name": display_caller_name(r),
            "employee_id": r["employee_id"] or "",
            "status": r["status"] or "failed",
            "ticket_number": r["ticket_number"] or "",
            "summary": r["summary"] or "",
            "started_at": human_time(r["start_time"]),
        })

    return render_template(
        request, "failed_calls.html",
        {
            "title": "Failed & Escalated",
            "active_page": "failed_calls",
            "user": user,
            "rows": formatted_rows,
        }
    )


# -----------------------------------------------------------------------------
# Recordings & Reviews
# -----------------------------------------------------------------------------

@app.get("/recordings", response_class=HTMLResponse)
def recordings(request: Request):
    user = require_roles(request, ["admin", "quality_reviewer"])
    enforce_recording_retention()
    files = recording_files()

    return render_template(
        request, "recordings.html",
        {
            "title": "Recordings & QA",
            "active_page": "recordings",
            "user": user,
            "files": files,
        }
    )


@app.get("/recordings/play")
def play_recording(request: Request, file: str):
    user = require_roles(request, ["admin", "quality_reviewer"])
    safe_name = Path(file).name
    full_path = Path(RECORDING_DIR) / safe_name
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Recording not found")
    audit(user["username"], "play_recording", "recording", safe_name)
    return FileResponse(path=str(full_path), media_type="audio/wav", filename=safe_name)


@app.post("/quality/review")
def save_review(
    request: Request,
    recording_file: str = Form(...),
    rating: str = Form(...),
    notes: str = Form(""),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    user = require_roles(request, ["admin", "quality_reviewer"])
    conn = db()
    conn.execute(
        """
        INSERT INTO quality_reviews(recording_file, reviewer, rating, notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (Path(recording_file).name, user["username"], rating, notes, int(time.time())),
    )
    conn.commit()
    conn.close()
    audit(user["username"], "save_quality_review", "recording", recording_file)
    return RedirectResponse("/recordings", status_code=302)


# -----------------------------------------------------------------------------
# Health and Security
# -----------------------------------------------------------------------------

@app.get("/health", response_class=HTMLResponse)
def health_page(request: Request):
    user = require_roles(request, ["admin"])
    bridge_ok = check_tcp_port("127.0.0.1", 8765)
    bridge_status = check_systemd_service("ai-support-bridge")
    asterisk_status = check_systemd_service("asterisk")
    dashboard_status = check_systemd_service("ai-dashboard")
    frappe_url = os.getenv("FRAPPE_URL", "http://127.0.0.1:8000")
    frappe_ok = check_url(f"{frappe_url}/api/method/ping") or check_url(frappe_url)
    openai_key_configured = bool(os.getenv("OPENAI_API_KEY"))

    health_info = {
        "bridge_ok": bridge_ok,
        "bridge_status": "Operational" if (bridge_status == "active" or bridge_ok) else "Degraded",
        "asterisk_status": "Operational" if asterisk_status == "active" else "Degraded",
        "dashboard_status": "Operational",
        "frappe_url": "Configured IT Helpdesk",
        "frappe_ok": frappe_ok,
        "openai_key_configured": openai_key_configured,
    }

    return render_template(
        request, "health.html",
        {
            "title": "System Health",
            "active_page": "health",
            "user": user,
            "health": health_info,
        }
    )


@app.get("/security-events", response_class=HTMLResponse)
def security_events_page(request: Request):
    user = require_roles(request, ["admin"])
    conn = db()
    raw_rows = conn.execute(
        """
        SELECT * FROM security_events 
        WHERE event_type IN (
            'dashboard_login_success',
            'dashboard_login_failed',
            'dashboard_login_rate_limited',
            'change_password_success',
            'change_password_failed',
            'admin_reset_user_password',
            'initial_admin_setup_success',
            'operator_created',
            'operator_updated'
        )
        ORDER BY id DESC LIMIT 300
        """
    ).fetchall()

    user_rows = conn.execute("SELECT username, role FROM users").fetchall()
    conn.close()

    role_map = {u["username"]: u["role"] for u in user_rows}

    rows = []
    for r in raw_rows:
        raw_details = r["details"] or ""
        parsed = {}
        for item in raw_details.split(","):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                parsed[k.strip()] = v.strip()

        operator = parsed.get("username") or parsed.get("admin") or parsed.get("operator") or "System"
        role_raw = role_map.get(operator, "admin" if operator == "admin" else "user")
        if role_raw == "admin":
            role_label = "Administrator"
        elif role_raw == "quality_reviewer":
            role_label = "Quality Reviewer"
        else:
            role_label = "Console User"

        key = r["key"] or ""
        ip_addr = key if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", key) else "127.0.0.1"

        event_type = r["event_type"]
        if event_type == "dashboard_login_success":
            action = "Sign In Successful"
            badge_variant = "success"
            description = f"Operator '{operator}' successfully authenticated into the operations console."
        elif event_type == "dashboard_login_failed":
            action = "Sign In Failed"
            badge_variant = "destructive"
            description = f"Authentication rejected: invalid credentials attempted for operator '{operator}'."
        elif event_type == "dashboard_login_rate_limited":
            action = "Rate Limit Locked"
            badge_variant = "warning"
            description = "Console access temporarily suspended due to consecutive failed sign-in attempts."
        elif event_type == "change_password_success":
            action = "Password Updated"
            badge_variant = "info"
            description = f"Operator '{operator}' successfully updated their console password."
        elif event_type == "change_password_failed":
            action = "Password Change Failed"
            badge_variant = "destructive"
            description = "Password change rejected: invalid current password provided."
        elif event_type == "admin_reset_user_password":
            action = "Credentials Reset"
            badge_variant = "warning"
            target_id = parsed.get("target_id", "operator")
            description = f"Administrator reset access credentials for operator account #{target_id}."
        elif event_type == "initial_admin_setup_success":
            action = "System Provisioned"
            badge_variant = "success"
            description = "Primary administrative root credentials successfully initialized."
        elif event_type == "operator_created":
            action = "Operator Created"
            badge_variant = "info"
            target = parsed.get("operator", "account")
            role_assigned = parsed.get("role", "user")
            description = f"Administrator created new operator account '{target}' with {role_assigned} privileges."
        elif event_type == "operator_updated":
            action = "Role Updated"
            badge_variant = "info"
            target_id = parsed.get("operator_id", "")
            description = f"Administrator updated operator permissions and status for account #{target_id}."
        else:
            action = event_type.replace("_", " ").title()
            badge_variant = "secondary"
            description = "Console administrative operation executed."

        rows.append({
            "id": r["id"],
            "operator": operator,
            "role": role_label,
            "action": action,
            "badge_variant": badge_variant,
            "ip_address": ip_addr,
            "description": description,
            "created_at_human": human_time(r["created_at"]),
        })

    return render_template(
        request, "security_events.html",
        {
            "title": "Security Events",
            "active_page": "security",
            "user": user,
            "rows": rows,
        }
    )


# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    user = require_roles(request, ["admin"])

    keys = [
        "organization_name", "dashboard_title", "dashboard_subtitle",
        "ai_greeting", "system_prompt", "frappe_enabled",
        "ad_enabled", "ad_server", "ad_port", "ad_use_ssl", "ad_bind_dn",
        "ad_password", "ad_base_dn", "ad_search_filter", "ad_sync_interval_minutes",
    ]
    settings_data = {k: get_setting(k, "") for k in keys}

    from app.ad_sync import get_ad_telemetry
    ad_telemetry = get_ad_telemetry()

    return render_template(
        request, "settings.html",
        {
            "title": "Settings",
            "active_page": "settings",
            "user": user,
            "settings": settings_data,
            "ad": ad_telemetry,
        }
    )


@app.post("/settings")
async def save_settings(request: Request):
    user = require_roles(request, ["admin"])
    data = await request.form()
    validate_csrf(request, data.get("csrf_token"))

    allowed_keys = {
        "organization_name", "dashboard_title", "dashboard_subtitle", "profile_icon_text",
        "ai_greeting", "system_prompt", "frappe_enabled",
        "ad_enabled", "ad_server", "ad_port", "ad_use_ssl", "ad_bind_dn",
        "ad_password", "ad_base_dn", "ad_search_filter", "ad_sync_interval_minutes",
    }

    conn = db()
    for key, value in data.items():
        if key in allowed_keys:
            save_setting(conn, key, str(value))
    conn.commit()
    conn.close()
    audit(user["username"], "update_settings", "settings", "multiple")
    return RedirectResponse("/settings", status_code=302)


# -----------------------------------------------------------------------------
# Change Password (With Full Session Invalidation)
# -----------------------------------------------------------------------------

@app.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request):
    user = require_user(request)
    return render_template(
        request, "change_password.html",
        {
            "title": "Change Password",
            "active_page": "change_password",
            "user": user,
        }
    )


@app.post("/change-password", response_class=HTMLResponse)
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    user = require_user(request)
    client_ip = request.client.host if request.client else "127.0.0.1"

    if not check_rate_limit(f"change_pwd:{client_ip}", max_attempts=5, window_seconds=300):
        return render_template(
            request, "change_password.html",
            {"error": "Too many password attempts. Please wait 5 minutes.", "title": "Change Password", "user": user},
            status_code=429
        )

    if new_password != confirm_password:
        return render_template(
            request, "change_password.html",
            {"error": "New passwords do not match.", "title": "Change Password", "user": user},
            status_code=400
        )
    if len(new_password) < 10:
        return render_template(
            request, "change_password.html",
            {"error": "New password must be at least 10 characters.", "title": "Change Password", "user": user},
            status_code=400
        )
    if not verify_password(current_password, user["password_hash"]):
        log_security_event("change_password_failed", client_ip, f"username={user['username']}")
        return render_template(
            request, "change_password.html",
            {"error": "Current password is incorrect.", "title": "Change Password", "user": user},
            status_code=400
        )

    conn = db()
    # Invalidate all existing sessions for this user (terminating old stolen/stale sessions)
    conn.execute("DELETE FROM sessions WHERE username=?", (user["username"],))
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user["id"]))
    conn.commit()
    conn.close()

    audit(user["username"], "change_password", "user", str(user["id"]))
    log_security_event("change_password_success", client_ip, f"username={user['username']}")

    # Grant fresh session token to the current browser
    new_token = create_session(user["username"])
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        COOKIE_NAME,
        new_token,
        httponly=True,
        secure=is_request_secure(request),
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )
    return response


# -----------------------------------------------------------------------------
# User Management (With Full Session Invalidation on Reset)
# -----------------------------------------------------------------------------

@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    user = require_roles(request, ["admin"])

    conn = db()
    rows = conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()
    conn.close()

    return render_template(
        request, "users.html",
        {
            "title": "User Management",
            "active_page": "users",
            "user": user,
            "rows": rows,
        }
    )


@app.post("/users/add")
def add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])
    client_ip = request.client.host if request.client else "127.0.0.1"

    if not check_rate_limit(f"add_user:{client_ip}", max_attempts=10, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many user creation requests")

    username = username.strip()
    if not re.match(r"^[a-zA-Z0-9_.-]+$", username):
        raise HTTPException(status_code=400, detail="Invalid username characters")

    if role not in ["admin", "user", "quality_reviewer"]:
        raise HTTPException(status_code=400, detail="Invalid role")
    if len(password) < 10:
        raise HTTPException(status_code=400, detail="Password must be at least 10 characters")

    conn = db()
    try:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (username, hash_password(password), role, int(time.time())),
        )
        conn.commit()
    except (sqlite3.IntegrityError, Exception):
        conn.close()
        raise HTTPException(status_code=400, detail="Username already exists or database error")
    conn.close()

    audit(admin["username"], "add_user", "user", username)
    log_security_event("operator_created", client_ip, f"admin={admin['username']}, operator={username}, role={role}")
    return RedirectResponse("/users", status_code=302)


@app.post("/users/update")
def update_user(
    request: Request,
    user_id: int = Form(...),
    role: str = Form(...),
    active: str = Form(None),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])
    if role not in ["admin", "user", "quality_reviewer"]:
        raise HTTPException(status_code=400, detail="Invalid role")
    active_value = 1 if active == "1" else 0
    conn = db()
    conn.execute("UPDATE users SET role=?, active=? WHERE id=?", (role, active_value, user_id))
    
    # If user is deactivated, immediately kill their active sessions
    if active_value == 0:
        target_user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if target_user:
            conn.execute("DELETE FROM sessions WHERE username=?", (target_user["username"],))

    conn.commit()
    conn.close()
    audit(admin["username"], "update_user", "user", str(user_id))
    client_ip = request.client.host if request.client else "127.0.0.1"
    log_security_event("operator_updated", client_ip, f"admin={admin['username']}, operator_id={user_id}, role={role}, active={active_value}")
    return RedirectResponse("/users", status_code=302)


@app.post("/users/reset-password")
def reset_user_password(
    request: Request,
    user_id: int = Form(...),
    new_password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])
    client_ip = request.client.host if request.client else "127.0.0.1"

    if not check_rate_limit(f"reset_pwd:{client_ip}", max_attempts=10, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many reset password requests")

    if len(new_password) < 10:
        raise HTTPException(status_code=400, detail="Password must be at least 10 characters")

    conn = db()
    target_user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if target_user:
        # Invalidate all active sessions for this target user immediately
        conn.execute("DELETE FROM sessions WHERE username=?", (target_user["username"],))

    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user_id))
    conn.commit()
    conn.close()

    audit(admin["username"], "reset_password", "user", str(user_id))
    log_security_event("admin_reset_user_password", client_ip, f"admin={admin['username']}, target_id={user_id}")
    return RedirectResponse("/users", status_code=302)


# -----------------------------------------------------------------------------
# Caller Directory & Telephony VIP Roster
# -----------------------------------------------------------------------------

@app.get("/callers", response_class=HTMLResponse)
def callers_page(request: Request):
    user = require_roles(request, ["admin"])

    q = request.query_params.get("q", "").strip()
    tier_filter = request.query_params.get("tier", "ALL").strip()
    status_filter = request.query_params.get("status", "ALL").strip()
    dept_filter = request.query_params.get("department", "ALL").strip()

    conn = db()

    # Base query for filtered rows
    sql = "SELECT * FROM callers WHERE 1=1"
    params = []

    if q:
        sql += """
        AND (
            employee_id LIKE ? OR name LIKE ? OR aliases LIKE ?
            OR phone LIKE ? OR email LIKE ? OR role LIKE ?
        )
        """
        like = f"%{q}%"
        params.extend([like, like, like, like, like, like])

    if tier_filter != "ALL":
        sql += " AND tier = ?"
        params.append(tier_filter)

    if status_filter == "active":
        sql += " AND active = 1"
    elif status_filter == "inactive":
        sql += " AND active = 0"

    if dept_filter != "ALL":
        sql += " AND department = ?"
        params.append(dept_filter)

    sql += " ORDER BY id ASC"
    rows = conn.execute(sql, params).fetchall()

    # Aggregate telemetry counters from full dataset
    all_callers = conn.execute("SELECT tier, active, department FROM callers").fetchall()
    conn.close()

    total_count = len(all_callers)
    active_count = sum(1 for c in all_callers if c["active"] == 1)
    inactive_count = total_count - active_count
    vip_count = sum(1 for c in all_callers if (c["tier"] in ("P0_EXECUTIVE", "P1_VIP") and c["active"] == 1))

    departments = sorted(list(set(c["department"] for c in all_callers if c["department"])))

    # Process aliases for display as pill tags
    formatted_rows = []
    for r in rows:
        aliases_list = [a.strip() for a in (r["aliases"] or "").split("|") if a.strip()]
        formatted_rows.append({
            "id": r["id"],
            "employee_id": r["employee_id"],
            "name": r["name"],
            "aliases": aliases_list,
            "raw_aliases": r["aliases"] or "",
            "email": r["email"] or "",
            "phone": r["phone"] or "",
            "department": r["department"] or "",
            "role": r["role"] or "Employee",
            "tier": r["tier"] or "STANDARD",
            "vip": bool(r["vip"]),
            "active": r["active"],
            "source": r["source"] or "manual",
        })

    stats = {
        "total": total_count,
        "active": active_count,
        "vip": vip_count,
        "inactive": inactive_count,
    }

    filters = {
        "q": q,
        "tier": tier_filter,
        "status": status_filter,
        "department": dept_filter,
    }

    from app.ad_sync import get_ad_telemetry
    ad_telemetry = get_ad_telemetry()

    return render_template(
        request, "callers.html",
        {
            "title": "Caller Directory",
            "active_page": "callers",
            "user": user,
            "rows": formatted_rows,
            "stats": stats,
            "departments": departments,
            "filters": filters,
            "ad": ad_telemetry,
        }
    )


@app.post("/callers/add")
def add_caller(
    request: Request,
    employee_id: str = Form(...),
    name: str = Form(...),
    aliases: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    department: str = Form(""),
    role: str = Form("Employee"),
    tier: str = Form("STANDARD"),
    active: str = Form("1"),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])

    employee_id = employee_id.strip()
    name = name.strip()
    if not employee_id or not name:
        raise HTTPException(status_code=400, detail="Employee ID and Name are required")

    if tier not in ["STANDARD", "P1_VIP", "P0_EXECUTIVE"]:
        raise HTTPException(status_code=400, detail="Invalid tier")

    vip = 1 if tier in ("P0_EXECUTIVE", "P1_VIP") else 0
    active_val = 1 if active in ("1", "true", "on") else 0
    now = int(time.time())

    conn = db()
    try:
        conn.execute(
            """
            INSERT INTO callers (
                employee_id, name, aliases, email, phone,
                department, role, vip, tier, active, source, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                employee_id, name, aliases.strip(), email.strip(), phone.strip(),
                department.strip(), role.strip() or "Employee", vip, tier, active_val,
                "manual", now, now
            ),
        )
        conn.commit()
    except (sqlite3.IntegrityError, Exception):
        conn.close()
        raise HTTPException(status_code=400, detail="Employee ID already exists in directory")
    conn.close()

    app_db.sync_callers_to_csv()
    try:
        from app.verify import clear_cache
        clear_cache()
    except Exception:
        pass

    audit(admin["username"], "add_caller", "caller", employee_id)
    return RedirectResponse("/callers", status_code=302)


@app.post("/callers/update")
def update_caller(
    request: Request,
    caller_id: int = Form(...),
    name: str = Form(...),
    aliases: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    department: str = Form(""),
    role: str = Form("Employee"),
    tier: str = Form("STANDARD"),
    active: str = Form(None),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])

    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    if tier not in ["STANDARD", "P1_VIP", "P0_EXECUTIVE"]:
        raise HTTPException(status_code=400, detail="Invalid tier")

    vip = 1 if tier in ("P0_EXECUTIVE", "P1_VIP") else 0
    active_val = 1 if active == "1" else 0
    now = int(time.time())

    conn = db()
    conn.execute(
        """
        UPDATE callers
        SET name=?, aliases=?, email=?, phone=?, department=?, role=?, vip=?, tier=?, active=?, updated_at=?
        WHERE id=?
        """,
        (
            name, aliases.strip(), email.strip(), phone.strip(),
            department.strip(), role.strip() or "Employee", vip, tier, active_val, now, caller_id
        ),
    )
    conn.commit()
    conn.close()

    app_db.sync_callers_to_csv()
    try:
        from app.verify import clear_cache
        clear_cache()
    except Exception:
        pass

    audit(admin["username"], "update_caller", "caller", str(caller_id))
    return RedirectResponse("/callers", status_code=302)


@app.post("/callers/toggle-status")
def toggle_caller_status(
    request: Request,
    caller_id: int = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])

    conn = db()
    target = conn.execute("SELECT id, employee_id, active FROM callers WHERE id=?", (caller_id,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Caller record not found")

    new_status = 0 if target["active"] == 1 else 1
    now = int(time.time())
    conn.execute("UPDATE callers SET active=?, updated_at=? WHERE id=?", (new_status, now, caller_id))
    conn.commit()
    conn.close()

    app_db.sync_callers_to_csv()
    try:
        from app.verify import clear_cache
        clear_cache()
    except Exception:
        pass

    action = "offboard_caller" if new_status == 0 else "activate_caller"
    audit(admin["username"], action, "caller", target["employee_id"])
    return RedirectResponse("/callers", status_code=302)


@app.post("/callers/delete")
def delete_caller(
    request: Request,
    caller_id: int = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])

    conn = db()
    target = conn.execute("SELECT id, employee_id FROM callers WHERE id=?", (caller_id,)).fetchone()
    if target:
        conn.execute("DELETE FROM callers WHERE id=?", (caller_id,))
        conn.commit()
    conn.close()

    app_db.sync_callers_to_csv()
    try:
        from app.verify import clear_cache
        clear_cache()
    except Exception:
        pass

    if target:
        audit(admin["username"], "delete_caller", "caller", target["employee_id"])

    return RedirectResponse("/callers", status_code=302)


@app.get("/callers/export")
def export_callers(request: Request):
    require_roles(request, ["admin"])

    conn = db()
    rows = conn.execute(
        "SELECT employee_id, name, aliases, email, phone, department, vip, role, tier, active FROM callers ORDER BY id ASC"
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["employee_id", "name", "aliases", "email", "phone", "department", "vip", "role", "tier", "active"])
    for r in rows:
        writer.writerow([
            r["employee_id"],
            r["name"],
            r["aliases"] or "",
            r["email"] or "",
            r["phone"] or "",
            r["department"] or "",
            "true" if r["vip"] else "false",
            r["role"] or "Employee",
            r["tier"] or "STANDARD",
            "true" if r["active"] else "false",
        ])

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=calling_users_roster.csv"},
    )


@app.post("/callers/import")
async def import_callers(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])

    contents = await file.read()
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = contents.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    now = int(time.time())
    imported_count = 0

    conn = db()
    for r in reader:
        emp_id = str(r.get("employee_id") or "").strip()
        name = str(r.get("name") or "").strip()
        if not emp_id or not name:
            continue

        aliases = str(r.get("aliases") or "").strip()
        email = str(r.get("email") or "").strip()
        phone = str(r.get("phone") or "").strip()
        dept = str(r.get("department") or "").strip()
        role = str(r.get("role") or "Employee").strip()
        tier = str(r.get("tier") or "STANDARD").strip().upper()
        if tier not in ["STANDARD", "P1_VIP", "P0_EXECUTIVE"]:
            tier = "STANDARD"

        vip_raw = str(r.get("vip") or "false").strip().lower()
        vip = 1 if (vip_raw in ("true", "1", "yes", "y") or tier in ("P0_EXECUTIVE", "P1_VIP")) else 0

        active_raw = str(r.get("active") or "1").strip().lower()
        active = 0 if active_raw in ("false", "0", "no") else 1

        existing = conn.execute("SELECT id FROM callers WHERE employee_id = ?", (emp_id,)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE callers
                SET name=?, aliases=?, email=?, phone=?, department=?, role=?, vip=?, tier=?, active=?, updated_at=?
                WHERE id=?
                """,
                (name, aliases, email, phone, dept, role, vip, tier, active, now, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO callers (
                    employee_id, name, aliases, email, phone,
                    department, role, vip, tier, active, source, updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (emp_id, name, aliases, email, phone, dept, role, vip, tier, active, "csv_import", now, now),
            )
        imported_count += 1

    conn.commit()
    conn.close()

    app_db.sync_callers_to_csv()
    try:
        from app.verify import clear_cache
        clear_cache()
    except Exception:
        pass

    audit(admin["username"], "import_callers", "callers", f"count_{imported_count}")
    return RedirectResponse("/callers", status_code=302)


# -----------------------------------------------------------------------------
# Active Directory (AD / LDAP) Sync API Endpoints
# -----------------------------------------------------------------------------

@app.post("/api/ad/test-connection")
async def api_test_ad_connection(request: Request):
    require_roles(request, ["admin"])
    try:
        data = await request.json()
    except Exception:
        data = {}

    from app.ad_sync import test_ad_connection
    report = test_ad_connection(data if data else None)
    return JSONResponse(report)


@app.post("/api/ad/sync-now")
async def api_ad_sync_now(request: Request):
    admin = require_roles(request, ["admin"])

    from app.ad_sync import sync_active_directory
    try:
        result = sync_active_directory(triggered_by=admin["username"])
        audit(admin["username"], "ad_manual_sync", "active_directory", f"processed={result.get('total_scanned', 0)}")
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=500)


# -----------------------------------------------------------------------------
# Prompt Version History
# -----------------------------------------------------------------------------

@app.get("/prompts", response_class=HTMLResponse)
def prompts_page(request: Request):
    user = require_roles(request, ["admin"])
    conn = db()
    raw_rows = conn.execute("SELECT * FROM prompt_versions ORDER BY id DESC").fetchall()
    conn.close()

    rows = []
    for r in raw_rows:
        rows.append({
            "id": r["id"],
            "name": r["name"],
            "greeting": r["greeting"],
            "system_prompt": r["system_prompt"],
            "active": r["active"],
            "created_by": r["created_by"],
            "created_at_human": human_time(r["created_at"]),
        })

    current_greeting = get_setting("ai_greeting", "")
    current_prompt = get_setting("system_prompt", "")

    return render_template(
        request, "prompts.html",
        {
            "title": "AI Prompts",
            "active_page": "prompts",
            "user": user,
            "rows": rows,
            "current_greeting": current_greeting,
            "current_prompt": current_prompt,
        }
    )


@app.post("/prompts/add")
def add_prompt_version(
    request: Request,
    name: str = Form(...),
    greeting: str = Form(...),
    system_prompt: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    user = require_roles(request, ["admin"])
    conn = db()
    conn.execute(
        "INSERT INTO prompt_versions(name, greeting, system_prompt, active, created_by, created_at) VALUES (?, ?, ?, 0, ?, ?)",
        (name, greeting, system_prompt, user["username"], int(time.time())),
    )
    conn.commit()
    conn.close()
    audit(user["username"], "add_prompt_version", "prompt", name)
    return RedirectResponse("/prompts", status_code=302)


@app.post("/prompts/activate")
def activate_prompt(request: Request, prompt_id: int = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    user = require_roles(request, ["admin"])
    conn = db()
    row = conn.execute("SELECT * FROM prompt_versions WHERE id=?", (prompt_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Prompt not found")

    conn.execute("UPDATE prompt_versions SET active=0")
    conn.execute("UPDATE prompt_versions SET active=1 WHERE id=?", (prompt_id,))
    save_setting(conn, "ai_greeting", row["greeting"])
    save_setting(conn, "system_prompt", row["system_prompt"])
    conn.commit()
    conn.close()
# -----------------------------------------------------------------------------
# Knowledge Base Management & Playbook Repository
# -----------------------------------------------------------------------------

@app.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(
    request: Request,
    category: str = "",
    status: str = "all",
    q: str = "",
):
    user = require_roles(request, ["admin", "user", "quality_reviewer"])

    all_articles = app_db.list_knowledge_articles(active_only=False)

    # Compute Statistics
    total_articles = len(all_articles)
    active_articles = sum(1 for a in all_articles if a.get("active") == 1)
    inactive_articles = total_articles - active_articles
    unique_categories = sorted(list({a.get("category", "General IT") for a in all_articles if a.get("category")}))
    categories_count = len(unique_categories)

    latest_update = max([a.get("updated_at") or 0 for a in all_articles], default=0)
    last_updated_human = human_time(latest_update) if latest_update else "Never"

    # Filter articles
    filtered = []
    q_clean = q.strip().lower()
    for a in all_articles:
        if category and a.get("category") != category:
            continue
        if status == "active" and a.get("active") != 1:
            continue
        if status == "inactive" and a.get("active") != 0:
            continue
        if q_clean:
            haystack = f"{a.get('article_id', '')} {a.get('title', '')} {a.get('category', '')} {a.get('keywords_en', '')} {a.get('keywords_ar', '')} {a.get('content', '')}".lower()
            if q_clean not in haystack:
                continue

        raw_en = a.get("keywords_en") or ""
        raw_ar = a.get("keywords_ar") or ""
        kw_en_list = [k.strip() for k in raw_en.split(",") if k.strip()] if isinstance(raw_en, str) else list(raw_en)
        kw_ar_list = [k.strip() for k in raw_ar.split(",") if k.strip()] if isinstance(raw_ar, str) else list(raw_ar)

        filtered.append({
            "id": a.get("id"),
            "article_id": a.get("article_id"),
            "title": a.get("title"),
            "category": a.get("category", "General IT"),
            "keywords_en": raw_en,
            "keywords_ar": raw_ar,
            "keywords_en_list": kw_en_list,
            "keywords_ar_list": kw_ar_list,
            "content": a.get("content", ""),
            "active": a.get("active", 1),
            "created_by": a.get("created_by", "system"),
            "updated_at": a.get("updated_at"),
            "updated_at_human": human_time(a.get("updated_at")),
        })

    stats = {
        "total": total_articles,
        "active": active_articles,
        "inactive": inactive_articles,
        "categories_count": categories_count,
        "last_updated": last_updated_human,
    }

    filters = {
        "category": category,
        "status": status,
        "q": q,
    }

    return render_template(
        request, "knowledge.html",
        {
            "title": "Knowledge Base Management",
            "active_page": "knowledge",
            "user": user,
            "rows": filtered,
            "stats": stats,
            "categories": unique_categories,
            "filters": filters,
        }
    )


@app.get("/api/knowledge")
def api_list_knowledge(request: Request, active_only: bool = False):
    require_roles(request, ["admin", "user", "quality_reviewer"])
    articles = app_db.list_knowledge_articles(active_only=active_only)
    result = []
    for a in articles:
        result.append({
            "article_id": a.get("article_id"),
            "title": a.get("title"),
            "category": a.get("category"),
            "keywords_en": a.get("keywords_en"),
            "keywords_ar": a.get("keywords_ar"),
            "active": a.get("active"),
            "updated_at": a.get("updated_at"),
            "updated_at_human": human_time(a.get("updated_at")),
        })
    return JSONResponse(result)


@app.get("/api/knowledge/{article_id}")
def api_get_knowledge_article(request: Request, article_id: str):
    require_roles(request, ["admin", "user", "quality_reviewer"])
    article = app_db.get_knowledge_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail=f"Knowledge article '{article_id}' not found")
    return JSONResponse({
        "id": article.get("id"),
        "article_id": article.get("article_id"),
        "title": article.get("title"),
        "category": article.get("category"),
        "keywords_en": article.get("keywords_en"),
        "keywords_ar": article.get("keywords_ar"),
        "content": article.get("content"),
        "active": article.get("active"),
        "created_by": article.get("created_by"),
        "updated_at": article.get("updated_at"),
        "updated_at_human": human_time(article.get("updated_at")),
    })


@app.post("/knowledge/add")
def add_knowledge_article_route(
    request: Request,
    article_id: str = Form(...),
    title: str = Form(...),
    category: str = Form("General IT"),
    keywords_en: str = Form(""),
    keywords_ar: str = Form(""),
    content: str = Form(...),
    active: str = Form("1"),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])

    clean_id = article_id.strip().lower().replace(" ", "_")
    if not clean_id or not re.match(r"^[a-z0-9_-]+$", clean_id):
        raise HTTPException(status_code=400, detail="Invalid article ID. Use letters, numbers, hyphens and underscores.")
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Content is required")

    active_val = 1 if active in ("1", "true", "on") else 0

    app_db.save_knowledge_article(
        article_id=clean_id,
        title=title.strip(),
        category=category.strip() or "General IT",
        keywords_en=keywords_en.strip(),
        keywords_ar=keywords_ar.strip(),
        content=content.strip(),
        active=active_val,
        created_by=admin["username"],
    )

    try:
        from app.openai_realtime_bridge import invalidate_knowledge_cache
        invalidate_knowledge_cache()
    except Exception:
        pass

    audit(admin["username"], "add_knowledge_article", "knowledge_article", clean_id)
    return RedirectResponse("/knowledge", status_code=302)


@app.post("/knowledge/update")
def update_knowledge_article_route(
    request: Request,
    article_id: str = Form(...),
    title: str = Form(...),
    category: str = Form("General IT"),
    keywords_en: str = Form(""),
    keywords_ar: str = Form(""),
    content: str = Form(...),
    active: str = Form(None),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])

    clean_id = article_id.strip().lower().replace(" ", "_")
    if not clean_id:
        raise HTTPException(status_code=400, detail="Article ID is required")
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Content is required")

    active_val = 1 if active in ("1", "true", "on") else 0

    app_db.save_knowledge_article(
        article_id=clean_id,
        title=title.strip(),
        category=category.strip() or "General IT",
        keywords_en=keywords_en.strip(),
        keywords_ar=keywords_ar.strip(),
        content=content.strip(),
        active=active_val,
        created_by=admin["username"],
    )

    try:
        from app.openai_realtime_bridge import invalidate_knowledge_cache
        invalidate_knowledge_cache()
    except Exception:
        pass

    audit(admin["username"], "update_knowledge_article", "knowledge_article", clean_id)
    return RedirectResponse("/knowledge", status_code=302)


@app.post("/knowledge/toggle-status")
def toggle_knowledge_status_route(
    request: Request,
    article_id: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])

    clean_id = article_id.strip().lower()
    new_active = app_db.toggle_knowledge_article(clean_id)
    if new_active is None:
        raise HTTPException(status_code=404, detail=f"Article '{clean_id}' not found")

    try:
        from app.openai_realtime_bridge import invalidate_knowledge_cache
        invalidate_knowledge_cache()
    except Exception:
        pass

    action = "activate_knowledge_article" if new_active == 1 else "deactivate_knowledge_article"
    audit(admin["username"], action, "knowledge_article", clean_id)
    return RedirectResponse("/knowledge", status_code=302)


@app.post("/knowledge/delete")
def delete_knowledge_article_route(
    request: Request,
    article_id: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    admin = require_roles(request, ["admin"])

    clean_id = article_id.strip().lower()
    deleted = app_db.delete_knowledge_article(clean_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Article '{clean_id}' not found")

    try:
        from app.openai_realtime_bridge import invalidate_knowledge_cache
        invalidate_knowledge_cache()
    except Exception:
        pass

    audit(admin["username"], "delete_knowledge_article", "knowledge_article", clean_id)
    return RedirectResponse("/knowledge", status_code=302)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard.app:app", host=DASHBOARD_HOST, port=DASHBOARD_PORT, reload=False)

