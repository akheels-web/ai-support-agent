import os
import csv
import io
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

from fastapi import FastAPI, Request, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import validate_dashboard_config, DASHBOARD_SECRET, DASHBOARD_COOKIE_SECURE
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


# -----------------------------------------------------------------------------
# Auth helpers
# -----------------------------------------------------------------------------

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
    conn.execute(
        """
        INSERT INTO audit_logs(username, action, entity_type, entity_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (username, action, entity_type, entity_id, int(time.time())),
    )
    conn.commit()
    conn.close()


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
# Display & Telemetry formatting helpers
# -----------------------------------------------------------------------------

def human_time(ts):
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return ""


def extract_caller_from_recording(recording_file):
    if not recording_file:
        return ""
    name = Path(recording_file).name
    parts = name.split("-")
    if len(parts) >= 4:
        return parts[2]
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
        caller = "unknown"
        parts = file.name.split("-")
        if len(parts) >= 4:
            caller = parts[2]

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
        secure=DASHBOARD_COOKIE_SECURE,
        samesite="strict",
        max_age=SESSION_TTL_SECONDS,
    )
    return response


# -----------------------------------------------------------------------------
# Database Initialization
# -----------------------------------------------------------------------------

def init_db():
    app_db.init_all_tables()
    conn = db()

    default_users = [
        ("admin", "admin123", "admin"),
        ("user", "user123", "user"),
        ("reviewer", "reviewer123", "quality_reviewer"),
    ]

    for username, password, role in default_users:
        exists = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO users(username, password_hash, role, active, created_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (username, hash_password(password), role, int(time.time())),
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


# -----------------------------------------------------------------------------
# Auth routes
# -----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user["role"] == "quality_reviewer":
        return RedirectResponse("/recordings", status_code=302)
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=302)
    return render_template(request, "login.html", {"title": "Login"})


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
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
        secure=DASHBOARD_COOKIE_SECURE,
        samesite="strict",
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
# REST API Endpoints (For Charts, Live Auto-Refresh, and Call Drawers)
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
    # Fetch call history for the last 24h
    rows = conn.execute(
        """
        SELECT start_time, status, ai_deflected, resolution_type, tier, transferred, ticket_created
        FROM calls
        WHERE start_time >= ?
        ORDER BY start_time ASC
        """,
        (one_day_ago,),
    ).fetchall()

    # Breakdown overall
    deflected_count = conn.execute("SELECT COUNT(*) c FROM calls WHERE ai_deflected=1 OR resolution_type='AI_Resolved'").fetchone()["c"]
    transferred_l1 = conn.execute("SELECT COUNT(*) c FROM calls WHERE transferred=1 AND tier NOT IN ('P0_EXECUTIVE','P1_VIP','CRITICAL')").fetchone()["c"]
    transferred_vip = conn.execute("SELECT COUNT(*) c FROM calls WHERE transferred=1 AND tier IN ('P0_EXECUTIVE','P1_VIP')").fetchone()["c"]
    transferred_emerg = conn.execute("SELECT COUNT(*) c FROM calls WHERE tier='CRITICAL' OR status='emergency_escalated'").fetchone()["c"]
    tickets_count = conn.execute("SELECT COUNT(*) c FROM calls WHERE ticket_created=1").fetchone()["c"]
    conn.close()

    # Generate 12 2-hour buckets for last 24h trend
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
        result.append({
            "id": r["id"],
            "call_id": r["call_id"] or "",
            "caller_id": display_caller_id(r),
            "caller_name": display_caller_name(r),
            "employee_id": r["employee_id"] or "",
            "status": r["status"] or "in_progress",
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
    row = conn.execute(
        "SELECT * FROM calls WHERE call_id=? OR id=?",
        (call_identifier, call_identifier),
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
        "status": row["status"] or "completed",
        "ticket_number": row["ticket_number"] or "",
        "summary": row["summary"] or "",
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
        "bridge_status": bridge_status,
        "asterisk_status": asterisk_status,
        "dashboard_status": dashboard_status,
        "frappe_url": frappe_url,
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
    raw_rows = conn.execute("SELECT * FROM security_events ORDER BY id DESC LIMIT 300").fetchall()
    conn.close()

    rows = []
    for r in raw_rows:
        rows.append({
            "id": r["id"],
            "event_type": r["event_type"],
            "key": r["key"],
            "details": r["details"],
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
        "ai_greeting", "system_prompt", "max_concurrent_calls",
        "recording_retention_days", "frappe_enabled",
    ]
    settings_data = {k: get_setting(k, "") for k in keys}

    return render_template(
        request, "settings.html",
        {
            "title": "Settings",
            "active_page": "settings",
            "user": user,
            "settings": settings_data,
        }
    )


@app.post("/settings")
async def save_settings(request: Request):
    user = require_roles(request, ["admin"])
    data = await request.form()
    validate_csrf(request, data.get("csrf_token"))

    allowed_keys = {
        "organization_name", "dashboard_title", "dashboard_subtitle", "profile_icon_text",
        "ai_greeting", "system_prompt", "max_concurrent_calls", "recording_retention_days", "frappe_enabled",
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
# Change Password
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

    if new_password != confirm_password:
        return render_template(
            request, "change_password.html",
            {"error": "New passwords do not match.", "title": "Change Password", "user": user},
            status_code=400
        )
    if len(new_password) < 8:
        return render_template(
            request, "change_password.html",
            {"error": "New password must be at least 8 characters.", "title": "Change Password", "user": user},
            status_code=400
        )
    if not verify_password(current_password, user["password_hash"]):
        return render_template(
            request, "change_password.html",
            {"error": "Current password is incorrect.", "title": "Change Password", "user": user},
            status_code=400
        )

    conn = db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user["id"]))
    conn.commit()
    conn.close()
    audit(user["username"], "change_password", "user", str(user["id"]))
    return RedirectResponse("/dashboard", status_code=302)


# -----------------------------------------------------------------------------
# User Management
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
    if role not in ["admin", "user", "quality_reviewer"]:
        raise HTTPException(status_code=400, detail="Invalid role")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    conn = db()
    try:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (username.strip(), hash_password(password), role, int(time.time())),
        )
        conn.commit()
    except (sqlite3.IntegrityError, Exception):
        conn.close()
        raise HTTPException(status_code=400, detail="Username already exists or database error")
    conn.close()
    audit(admin["username"], "add_user", "user", username)
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
    conn.commit()
    conn.close()
    audit(admin["username"], "update_user", "user", str(user_id))
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
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    conn = db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user_id))
    conn.commit()
    conn.close()
    audit(admin["username"], "reset_password", "user", str(user_id))
    return RedirectResponse("/users", status_code=302)


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
    audit(user["username"], "activate_prompt", "prompt", str(prompt_id))
    return RedirectResponse("/prompts", status_code=302)
