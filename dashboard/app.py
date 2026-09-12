import os
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
from html import escape

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import validate_dashboard_config, DASHBOARD_SECRET, DASHBOARD_COOKIE_SECURE
from app.security_guard import check_rate_limit, log_security_event, reset_rate_limit, init_security_db

validate_dashboard_config()

BASE_DIR = Path(__file__).resolve().parent.parent
APP_SECRET = DASHBOARD_SECRET
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "dashboard.db"))
RECORDING_DIR = os.getenv("RECORDING_DIR", "/var/spool/asterisk/monitor/ai-support")
BRAND_ASSETS_DIR = os.getenv("BRAND_ASSETS_DIR", str(BASE_DIR / "brand-assets"))

NF_LOGO_LOCAL = "/brand-assets/nfc-logo.svg"
TCT_LOGO_LOCAL = "/brand-assets/tct-logo.png"
COOKIE_NAME = "ai_dashboard_token"
CSRF_COOKIE_NAME = "csrf_token"
SESSION_TTL_SECONDS = 28800

app = FastAPI(title="AI IT Support Dashboard")

Path(BRAND_ASSETS_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/brand-assets", StaticFiles(directory=BRAND_ASSETS_DIR), name="brand-assets")


import app.db as app_db

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


def csrf_input(token):
    return f'<input type="hidden" name="csrf_token" value="{escape(token)}">'


def validate_csrf(request: Request, submitted_token):
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token or not submitted_token:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    if not hmac.compare_digest(cookie_token, submitted_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def html_response_with_csrf(request: Request, content: str):
    token = ensure_csrf_token(request)
    response = HTMLResponse(content)
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        httponly=True,
        secure=DASHBOARD_COOKIE_SECURE,
        samesite="strict",
        max_age=SESSION_TTL_SECONDS,
    )
    return response


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
# Initialization
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
        "dashboard_title": "AI IT Support Dashboard",
        "dashboard_subtitle": "Voice AI Support Operations",
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


# -----------------------------------------------------------------------------
# Display helpers
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
        return ""
    try:
        return f"{(int(time.time()) - int(start_time)) / 60:.1f}"
    except Exception:
        return ""


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
                "path": str(file),
                "caller": caller,
                "size_mb": round(stat.st_size / 1024 / 1024, 2),
                "created_at": stat.st_mtime,
            }
        )
    return files


def selected_attr(current, value):
    return "selected" if str(current) == str(value) else ""


# -----------------------------------------------------------------------------
# Health helpers
# -----------------------------------------------------------------------------

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


def health_badge(ok):
    if ok:
        return "<span class='status-pill success'>Healthy</span>"
    return "<span class='status-pill danger'>Issue</span>"


# -----------------------------------------------------------------------------
# Layout
# -----------------------------------------------------------------------------

def layout(title, user, body):
    role = user["role"] if user else ""
    organization_name = get_setting("organization_name", "National Finance Oman")
    dashboard_title = get_setting("dashboard_title", "AI IT Support Dashboard")

    nav = ""
    if user:
        nav_links = '<a href="/dashboard">Dashboard</a>'
        if role in ["admin", "user"]:
            nav_links += '<a href="/calls">Calls</a>'
            nav_links += '<a href="/active-calls">Active Calls</a>'
            nav_links += '<a href="/failed-calls">Failed Calls</a>'
        if role in ["admin", "quality_reviewer"]:
            nav_links += '<a href="/recordings">Recordings</a>'
        if role == "admin":
            nav_links += '<a href="/health">Health</a>'
            nav_links += '<a href="/security-events">Security</a>'
            nav_links += '<a href="/prompts">Prompts</a>'
            nav_links += '<a href="/users">Users</a>'
            nav_links += '<a href="/settings">Settings</a>'
        nav_links += '<a href="/change-password">Change Password</a>'

        nav = f"""
        <div class="nav">
            <div class="brand">
                <img class="brand-logo" src="{NF_LOGO_LOCAL}" alt="National Finance Oman">
                <div>
                    <div class="brand-title">{escape(organization_name)}</div>
                    <div class="brand-subtitle">{escape(dashboard_title)}</div>
                </div>
            </div>
            <div class="nav-links">{nav_links}</div>
            <div class="nav-user">
                <span>{escape(user['username'])} ({escape(role)})</span>
                <a class="logout" href="/logout">Logout</a>
            </div>
        </div>
        """

    footer = f"""
    <div class="footer-credit">
        <span>Presented by</span>
        <img src="{TCT_LOGO_LOCAL}" alt="TCT Enterprise">
    </div>
    """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{escape(title)}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            :root {{
                --nf-navy: #1B2F6B;
                --nf-navy-dark: #132250;
                --nf-blue: #2B4A9F;
                --nf-red: #C8102E;
                --nf-bg: #F4F6FB;
                --nf-card: #FFFFFF;
                --nf-border: #D1D9F0;
                --nf-text: #1F2937;
                --nf-muted: #6B7280;
                --nf-success: #10B981;
                --nf-danger: #C8102E;
            }}
            * {{ box-sizing: border-box; }}
            body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 0; background: var(--nf-bg); color: var(--nf-text); }}
            .nav {{ background: linear-gradient(90deg, var(--nf-navy-dark) 0%, var(--nf-navy) 75%); color: white; padding: 14px 24px; display: flex; align-items: center; gap: 24px; box-shadow: 0 2px 14px rgba(0,0,0,.18); position: sticky; top: 0; z-index: 10; }}
            .brand {{ display: flex; align-items: center; gap: 12px; min-width: 320px; }}
            .brand-logo {{ height: 42px; max-width: 150px; background: white; border-radius: 10px; padding: 6px 10px; object-fit: contain; border-top: 4px solid var(--nf-red); }}
            .brand-title {{ font-size: 15px; font-weight: 800; }}
            .brand-subtitle {{ font-size: 11px; opacity: .78; margin-top: 2px; }}
            .nav-links {{ display: flex; align-items: center; gap: 14px; flex: 1; flex-wrap: wrap; }}
            .nav a {{ color: white; text-decoration: none; font-weight: 600; font-size: 14px; opacity: .9; }}
            .nav a:hover {{ opacity: 1; color: #dce7ff; }}
            .nav-user {{ display: flex; align-items: center; gap: 14px; font-size: 13px; }}
            .logout {{ background: rgba(255,255,255,.12); padding: 8px 12px; border-radius: 8px; }}
            .container {{ padding: 28px; max-width: 1500px; margin: 0 auto; min-height: calc(100vh - 130px); }}
            h1 {{ margin: 0 0 4px; color: var(--nf-navy); font-size: 28px; font-weight: 800; }}
            h2, h3 {{ color: var(--nf-navy); }}
            .subtitle {{ margin: 0 0 22px; color: var(--nf-muted); font-size: 14px; }}
            .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; margin-bottom: 22px; }}
            .metric {{ background: var(--nf-card); border-radius: 16px; padding: 22px; box-shadow: 0 4px 16px rgba(27,47,107,.10); border: 1px solid var(--nf-border); border-top: 4px solid var(--nf-red); }}
            .metric h2 {{ margin: 0; font-size: 34px; color: var(--nf-navy); font-weight: 800; }}
            .metric p {{ margin: 8px 0 0; color: var(--nf-muted); font-weight: 600; font-size: 14px; }}
            .card {{ background: var(--nf-card); border-radius: 16px; padding: 22px; margin-bottom: 22px; box-shadow: 0 4px 16px rgba(27,47,107,.10); border: 1px solid var(--nf-border); }}
            .filter-box {{ display: grid; grid-template-columns: 2fr 1fr 1fr 1fr 1fr; gap: 12px; align-items: end; margin-bottom: 12px; }}
            .filter-actions {{ display: flex; gap: 10px; align-items: center; margin-bottom: 18px; }}
            table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 12px; overflow: hidden; }}
            th, td {{ padding: 12px; border-bottom: 1px solid #e5e7eb; text-align: left; font-size: 14px; vertical-align: top; }}
            th {{ background: var(--nf-navy); color: white; font-weight: 700; white-space: nowrap; }}
            tr:hover td {{ background: #E8EDF8; }}
            .status-pill {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; background: #E8EDF8; color: var(--nf-navy); }}
            .ticket-pill {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 800; background: #FEE2E2; color: var(--nf-red); }}
            .success {{ background: #D1FAE5 !important; color: #065F46 !important; }}
            .danger {{ background: #FEE2E2 !important; color: var(--nf-danger) !important; }}
            input, textarea, select {{ width: 100%; padding: 10px; margin: 6px 0 12px; border: 1px solid var(--nf-border); border-radius: 8px; font-family: inherit; }}
            textarea {{ min-height: 95px; }}
            label {{ font-weight: 700; color: var(--nf-navy); font-size: 13px; }}
            button {{ background: var(--nf-navy); color: white; border: none; padding: 10px 18px; border-radius: 8px; cursor: pointer; font-weight: 700; min-height: 40px; }}
            button:hover {{ background: var(--nf-blue); }}
            .btn-link {{ display: inline-flex; align-items: center; min-height: 40px; background: #E8EDF8; color: var(--nf-navy); text-decoration: none; padding: 0 14px; border-radius: 8px; font-weight: 700; font-size: 13px; }}
            .btn-link:hover {{ background: #D1D9F0; }}
            .danger-note {{ color: var(--nf-danger); font-weight: bold; }}
            .small {{ color: var(--nf-muted); font-size: 13px; }}
            audio {{ width: 300px; }}
            .footer-credit {{ display: flex; align-items: center; justify-content: center; gap: 10px; color: var(--nf-muted); font-size: 12px; padding: 18px; }}
            .footer-credit img {{ height: 28px; max-width: 130px; object-fit: contain; }}
            @media (max-width: 1100px) {{ .grid {{ grid-template-columns: repeat(2, 1fr); }} .filter-box {{ grid-template-columns: 1fr 1fr; }} }}
            @media (max-width: 700px) {{ .grid {{ grid-template-columns: 1fr; }} .container {{ padding: 18px; }} .nav {{ flex-wrap: wrap; }} .brand {{ min-width: auto; }} .filter-box {{ grid-template-columns: 1fr; }} table {{ display: block; overflow-x: auto; }} }}
        </style>
    </head>
    <body>
        {nav}
        <div class="container">{body}</div>
        {footer}
    </body>
    </html>
    """


# -----------------------------------------------------------------------------
# Startup
# -----------------------------------------------------------------------------

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
    organization_name = get_setting("organization_name", "National Finance Oman")
    dashboard_title = get_setting("dashboard_title", "AI IT Support Dashboard")
    csrf_token = ensure_csrf_token(request)

    content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Login</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            :root {{ --nf-navy:#1B2F6B; --nf-blue:#2B4A9F; --nf-red:#C8102E; --nf-muted:#6B7280; }}
            * {{ box-sizing: border-box; }}
            body {{ margin:0; font-family:"Segoe UI",Arial,sans-serif; }}
            .login-wrap {{ min-height:100vh; display:flex; align-items:center; justify-content:center; background:radial-gradient(circle at center,#2B4A9F 0%,#132250 75%); padding:20px; position:relative; }}
            .login-card {{ max-width:430px; width:100%; background:white; border-radius:18px; padding:34px; box-shadow:0 24px 60px rgba(0,0,0,.30); border-top:5px solid var(--nf-red); }}
            .nf-logo {{ display:block; max-width:235px; max-height:85px; margin:0 auto 22px; object-fit:contain; }}
            h2 {{ margin:0 0 8px; color:var(--nf-navy); font-size:25px; text-align:center; }}
            p {{ margin-top:0; color:var(--nf-muted); font-size:14px; text-align:center; }}
            label {{ color:var(--nf-navy); font-weight:700; font-size:12px; text-transform:uppercase; display:block; margin-bottom:6px; }}
            input {{ width:100%; padding:10px; margin-bottom:14px; border:1.5px solid #b8c4e3; border-radius:8px; min-height:42px; font-size:14px; }}
            input:focus {{ border-color:var(--nf-blue); box-shadow:0 0 0 3px rgba(43,74,159,.15); outline:none; }}
            button {{ width:100%; min-height:44px; margin-top:10px; background:var(--nf-navy); color:white; border:none; border-radius:8px; cursor:pointer; font-weight:700; font-size:14px; }}
            button:hover {{ background:var(--nf-blue); }}
            .login-footer {{ position:fixed; left:0; right:0; bottom:22px; display:flex; align-items:center; justify-content:center; gap:10px; color:rgba(255,255,255,.78); font-size:12px; }}
            .login-footer img {{ height:32px; max-width:140px; object-fit:contain; background:rgba(255,255,255,.08); border-radius:6px; padding:3px 6px; }}
        </style>
    </head>
    <body>
        <div class="login-wrap">
            <div class="login-card">
                <img class="nf-logo" src="{NF_LOGO_LOCAL}" alt="National Finance Oman">
                <h2>{escape(dashboard_title)}</h2>
                <p>{escape(organization_name)} — Secure Operations Portal</p>
                <form method="post" action="/login">
                    {csrf_input(csrf_token)}
                    <label>Username</label>
                    <input name="username" required>
                    <label>Password</label>
                    <input name="password" type="password" required>
                    <button type="submit">Login</button>
                </form>
            </div>
            <div class="login-footer">
                <span>Presented by</span>
                <img src="{TCT_LOGO_LOCAL}" alt="TCT Enterprise">
            </div>
        </div>
    </body>
    </html>
    """
    return html_response_with_csrf(request, content)


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)

    client_ip = request.client.host if request.client else "unknown"
    rl = check_rate_limit(
        key=f"dashboard_login:{client_ip}",
        limit=5,
        window_seconds=300,
        lock_seconds=900,
    )

    if not rl["allowed"]:
        log_security_event("dashboard_login_rate_limited", client_ip, f"retry_after={rl['retry_after']}")
        return HTMLResponse("Too many login attempts. Please try again later.", status_code=429)

    conn = db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=? AND active=1",
        (username,),
    ).fetchone()
    conn.close()

    if not user or not verify_password(password, user["password_hash"]):
        log_security_event("dashboard_login_failed", client_ip, f"username={username}")
        return HTMLResponse('Invalid login. <a href="/login">Try again</a>', status_code=401)

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
# Main dashboard pages
# -----------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_roles(request, ["admin", "user"])
    organization_name = get_setting("organization_name", "National Finance Oman")
    dashboard_title = get_setting("dashboard_title", "AI IT Support Dashboard")
    dashboard_subtitle = get_setting("dashboard_subtitle", "Voice AI Support Operations")

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
    rejected = conn.execute("SELECT COUNT(*) c FROM calls WHERE status IN ('rejected','max_concurrent_rejected')").fetchone()["c"]
    transferred = conn.execute("SELECT COUNT(*) c FROM calls WHERE transferred=1").fetchone()["c"]
    vip_calls = conn.execute("SELECT COUNT(*) c FROM calls WHERE is_vip=1 OR tier IN ('P0_EXECUTIVE','P1_VIP')").fetchone()["c"]
    deflected = conn.execute("SELECT COUNT(*) c FROM calls WHERE ai_deflected=1 OR resolution_type='AI_Resolved'").fetchone()["c"]
    emergency_calls = conn.execute("SELECT COUNT(*) c FROM calls WHERE tier='CRITICAL' OR status='emergency_escalated'").fetchone()["c"]
    conn.close()

    metrics = f"""
    <div class="metric"><h2>{total_calls}</h2><p>Total Calls</p></div>
    <div class="metric"><h2>{ongoing}</h2><p>Ongoing Calls</p></div>
    <div class="metric"><h2>{tickets}</h2><p>Tickets Created</p></div>
    <div class="metric"><h2 style="color:var(--nf-success);">{deflected}</h2><p>AI Deflected / Resolved</p></div>
    <div class="metric"><h2>{verified}</h2><p>Verified Callers</p></div>
    <div class="metric"><h2>{vip_calls}</h2><p>VIP & Exec Calls</p></div>
    <div class="metric"><h2>{transferred}</h2><p>Transferred Calls</p></div>
    <div class="metric"><h2 style="color:var(--nf-danger);">{emergency_calls}</h2><p>Emergency Escalated</p></div>
    <div class="metric"><h2>{rejected}</h2><p>Rejected Calls</p></div>
    <div class="metric"><h2>{failed}</h2><p>Failed Calls</p></div>
    """

    if user["role"] == "admin":
        metrics += f'<div class="metric"><h2>{len(recording_files())}</h2><p>Recordings</p></div>'

    body = f"""
    <h1>{escape(dashboard_title)}</h1>
    <p class="subtitle">{escape(organization_name)} — {escape(dashboard_subtitle)}</p>
    <div class="grid">{metrics}</div>
    <div class="card">
        <h3>System Overview</h3>
        <ul>
            <li>AI voice agent is available for IT support calls.</li>
            <li>Tickets are created for unresolved incidents.</li>
            <li>Call metadata is logged for operational visibility.</li>
            <li>No delete actions are available in this dashboard.</li>
        </ul>
    </div>
    """
    return layout("Dashboard", user, body)


@app.get("/calls", response_class=HTMLResponse)
def calls(request: Request):
    user = require_roles(request, ["admin", "user"])

    q = request.query_params.get("q", "").strip()
    employee_id = request.query_params.get("employee_id", "").strip()
    status = request.query_params.get("status", "").strip()
    language = request.query_params.get("language", "").strip()
    ticket_created = request.query_params.get("ticket_created", "").strip()

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

    sql += " ORDER BY id ASC LIMIT 500"

    conn = db()
    rows = conn.execute(sql, params).fetchall()
    status_rows = conn.execute("SELECT DISTINCT status FROM calls WHERE status IS NOT NULL AND status != '' ORDER BY status").fetchall()
    conn.close()

    status_options = '<option value="">All Status</option>'
    for s in status_rows:
        status_options += f'<option value="{escape(s["status"])}" {selected_attr(status, s["status"])}>{escape(s["status"])}</option>'

    language_options = f"""
    <option value="">All Languages</option>
    <option value="en" {selected_attr(language, "en")}>English</option>
    <option value="ar" {selected_attr(language, "ar")}>Arabic</option>
    """
    ticket_options = f"""
    <option value="">All Tickets</option>
    <option value="1" {selected_attr(ticket_created, "1")}>Ticket Created</option>
    <option value="0" {selected_attr(ticket_created, "0")}>No Ticket</option>
    """

    table = """
    <table>
        <tr>
            <th>ID</th><th>Caller ID</th><th>Employee</th><th>Caller Name</th>
            <th>Language</th><th>Duration(mins)</th><th>Status</th><th>Ticket</th><th>Summary</th>
        </tr>
    """

    for r in rows:
        status_text = str(r["status"] or "")
        ticket = str(r["ticket_number"] or "")
        status_html = f"<span class='status-pill'>{escape(status_text)}</span>" if status_text else ""
        ticket_html = f"<span class='ticket-pill'>{escape(ticket)}</span>" if ticket else ""
        table += f"""
        <tr>
            <td>{r['id']}</td>
            <td>{escape(str(display_caller_id(r) or ''))}</td>
            <td>{escape(str(r['employee_id'] or ''))}</td>
            <td>{escape(display_caller_name(r))}</td>
            <td>{escape(str(r['language'] or ''))}</td>
            <td>{escape(format_minutes(r['duration_seconds']))}</td>
            <td>{status_html}</td>
            <td>{ticket_html}</td>
            <td>{escape(str(r['summary'] or '')[:160])}</td>
        </tr>
        """
    table += "</table>"

    body = f"""
    <h1>Call History</h1>
    <p class="subtitle">Recent AI support interactions with search and filters</p>
    <div class="card">
        <form method="get" action="/calls">
            <div class="filter-box">
                <div><label>Search</label><input name="q" value="{escape(q)}" placeholder="Caller, employee, ticket, status, summary"></div>
                <div><label>Employee ID</label><input name="employee_id" value="{escape(employee_id)}" placeholder="1002"></div>
                <div><label>Status</label><select name="status">{status_options}</select></div>
                <div><label>Language</label><select name="language">{language_options}</select></div>
                <div><label>Ticket</label><select name="ticket_created">{ticket_options}</select></div>
            </div>
            <div class="filter-actions">
                <button type="submit">Filter</button>
                <a class="btn-link" href="/calls">Reset</a>
            </div>
        </form>
        {table}
    </div>
    """
    return layout("Calls", user, body)


@app.get("/active-calls", response_class=HTMLResponse)
def active_calls_page(request: Request):
    user = require_roles(request, ["admin", "user"])
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

    table = """
    <table>
        <tr><th>ID</th><th>Call ID</th><th>Caller ID</th><th>Caller Name</th><th>Employee</th><th>Status</th><th>Duration(mins)</th><th>Started</th></tr>
    """
    for r in rows:
        table += f"""
        <tr>
            <td>{r['id']}</td>
            <td>{escape(str(r['call_id'] or ''))}</td>
            <td>{escape(str(display_caller_id(r) or ''))}</td>
            <td>{escape(display_caller_name(r))}</td>
            <td>{escape(str(r['employee_id'] or ''))}</td>
            <td><span class="status-pill">{escape(str(r['status'] or ''))}</span></td>
            <td>{escape(live_call_duration(r['start_time']))}</td>
            <td>{escape(human_time(r['start_time']))}</td>
        </tr>
        """
    table += "</table>"

    body = f"""
    <h1>Active Calls</h1>
    <p class="subtitle">Live calls detected within the last 30 minutes</p>
    <div class="card">{table}</div>
    """
    return layout("Active Calls", user, body)


@app.get("/failed-calls", response_class=HTMLResponse)
def failed_calls_page(request: Request):
    user = require_roles(request, ["admin", "user"])
    failed_statuses = [
        "failed", "openai_connection_failed", "openai_response_failed", "ticket_failed",
        "verification_failed", "verification_blocked", "audio_unclear", "transfer_failed",
        "rejected", "max_concurrent_rejected",
    ]
    placeholders = ",".join(["?"] * len(failed_statuses))

    conn = db()
    rows = conn.execute(
        f"SELECT * FROM calls WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT 300",
        failed_statuses,
    ).fetchall()
    conn.close()

    table = """
    <table>
        <tr><th>ID</th><th>Call ID</th><th>Caller ID</th><th>Caller Name</th><th>Employee</th><th>Status</th><th>Ticket</th><th>Summary / Error</th><th>Started</th></tr>
    """
    for r in rows:
        ticket = str(r["ticket_number"] or "")
        ticket_html = f"<span class='ticket-pill'>{escape(ticket)}</span>" if ticket else ""
        table += f"""
        <tr>
            <td>{r['id']}</td>
            <td>{escape(str(r['call_id'] or ''))}</td>
            <td>{escape(str(display_caller_id(r) or ''))}</td>
            <td>{escape(display_caller_name(r))}</td>
            <td>{escape(str(r['employee_id'] or ''))}</td>
            <td><span class="status-pill">{escape(str(r['status'] or ''))}</span></td>
            <td>{ticket_html}</td>
            <td>{escape(str(r['summary'] or '')[:220])}</td>
            <td>{escape(human_time(r['start_time']))}</td>
        </tr>
        """
    table += "</table>"

    body = f"""
    <h1>Failed / Rejected Calls</h1>
    <p class="subtitle">Calls that need review or troubleshooting</p>
    <div class="card">{table}</div>
    """
    return layout("Failed Calls", user, body)


# -----------------------------------------------------------------------------
# Recordings and reviews
# -----------------------------------------------------------------------------

@app.get("/recordings", response_class=HTMLResponse)
def recordings(request: Request):
    user = require_roles(request, ["admin", "quality_reviewer"])
    csrf_token = ensure_csrf_token(request)
    files = recording_files()

    table = """
    <table>
        <tr><th>File</th><th>Caller ID</th><th>Size MB</th><th>Playback</th><th>Review</th></tr>
    """
    for f in files:
        encoded = quote(f["name"])
        table += f"""
        <tr>
            <td>{escape(f['name'])}</td>
            <td>{escape(f['caller'])}</td>
            <td>{f['size_mb']}</td>
            <td><audio controls><source src="/recordings/play?file={encoded}" type="audio/wav"></audio></td>
            <td>
                <form method="post" action="/quality/review">
                    {csrf_input(csrf_token)}
                    <input type="hidden" name="recording_file" value="{escape(f['name'])}">
                    <select name="rating">
                        <option>Good</option><option>Needs Improvement</option><option>Escalated</option>
                    </select>
                    <textarea name="notes" placeholder="Review notes"></textarea>
                    <button type="submit">Save Review</button>
                </form>
            </td>
        </tr>
        """
    table += "</table>"

    body = f"""
    <h1>Recordings</h1>
    <p class="subtitle">Quality review and playback</p>
    <div class="card">
        <p class="danger-note">Delete is disabled by design. Recordings are read-only.</p>
        {table}
    </div>
    """
    return html_response_with_csrf(request, layout("Recordings", user, body))


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
# Health and security pages
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

    body = f"""
    <h1>System Health</h1>
    <p class="subtitle">Live health checks for AI support services</p>
    <div class="card">
        <table>
            <tr><th>Component</th><th>Status</th><th>Details</th></tr>
            <tr><td>AI Bridge Port 8765</td><td>{health_badge(bridge_ok)}</td><td>{'Listening' if bridge_ok else 'Not listening'}</td></tr>
            <tr><td>AI Bridge Service</td><td>{health_badge(bridge_status == 'active')}</td><td>{escape(bridge_status)}</td></tr>
            <tr><td>Asterisk Service</td><td>{health_badge(asterisk_status == 'active')}</td><td>{escape(asterisk_status)}</td></tr>
            <tr><td>Dashboard Service</td><td>{health_badge(dashboard_status == 'active')}</td><td>{escape(dashboard_status)}</td></tr>
            <tr><td>Frappe Helpdesk API</td><td>{health_badge(frappe_ok)}</td><td>{'Reachable' if frappe_ok else 'Not reachable'}</td></tr>
            <tr><td>OpenAI API Key</td><td>{health_badge(openai_key_configured)}</td><td>{'Configured' if openai_key_configured else 'Missing'} — key is not displayed</td></tr>
        </table>
    </div>
    """
    return layout("System Health", user, body)


@app.get("/security-events", response_class=HTMLResponse)
def security_events_page(request: Request):
    user = require_roles(request, ["admin"])
    conn = db()
    rows = conn.execute("SELECT * FROM security_events ORDER BY id DESC LIMIT 300").fetchall()
    conn.close()

    table = """
    <table>
        <tr><th>ID</th><th>Event</th><th>Key</th><th>Details</th><th>Time</th></tr>
    """
    for r in rows:
        table += f"""
        <tr>
            <td>{r['id']}</td>
            <td>{escape(r['event_type'] or '')}</td>
            <td>{escape(r['key'] or '')}</td>
            <td>{escape(r['details'] or '')}</td>
            <td>{escape(human_time(r['created_at']))}</td>
        </tr>
        """
    table += "</table>"

    body = f"""
    <h1>Security Events</h1>
    <p class="subtitle">Login failures, rate limits, and verification abuse events</p>
    <div class="card">{table}</div>
    """
    return layout("Security Events", user, body)


# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    user = require_roles(request, ["admin"])
    csrf_token = ensure_csrf_token(request)

    label_map = {
        "organization_name": "Organization Name",
        "dashboard_title": "Dashboard Title",
        "dashboard_subtitle": "Dashboard Subtitle",
        "profile_icon_text": "Profile Icon Text",
        "ai_greeting": "AI Greeting",
        "system_prompt": "System Prompt",
        "max_concurrent_calls": "Max Concurrent Calls",
        "recording_retention_days": "Recording Retention Days",
        "frappe_enabled": "Frappe Helpdesk Enabled",
    }
    editable_keys = list(label_map.keys())

    form = f'<form method="post" action="/settings">{csrf_input(csrf_token)}'
    for key in editable_keys:
        value = get_setting(key, "")
        form += f'<label>{escape(label_map[key])}</label><textarea name="{escape(key)}">{escape(value or "")}</textarea>'
    form += '<button type="submit">Save Settings</button></form>'

    body = f"""
    <h1>Settings</h1>
    <p class="subtitle">Admin-only configuration for dashboard, prompts, and AI behavior</p>
    <div class="card">
        <p class="small">Logo settings are fixed by system configuration and are not editable from the dashboard.</p>
        {form}
    </div>
    """
    return html_response_with_csrf(request, layout("Settings", user, body))


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
            save_setting(conn, key, value)
    conn.commit()
    conn.close()
    audit(user["username"], "update_settings", "settings", "multiple")
    return RedirectResponse("/settings", status_code=302)


# -----------------------------------------------------------------------------
# Change password
# -----------------------------------------------------------------------------

@app.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request):
    user = require_user(request)
    csrf_token = ensure_csrf_token(request)
    body = f"""
    <h1>Change Password</h1>
    <p class="subtitle">Update your dashboard password</p>
    <div class="card" style="max-width:520px;">
        <form method="post" action="/change-password">
            {csrf_input(csrf_token)}
            <label>Current Password</label><input type="password" name="current_password" required>
            <label>New Password</label><input type="password" name="new_password" required>
            <label>Confirm New Password</label><input type="password" name="confirm_password" required>
            <button type="submit">Change Password</button>
        </form>
    </div>
    """
    return html_response_with_csrf(request, layout("Change Password", user, body))


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
        return HTMLResponse('Passwords do not match. <a href="/change-password">Try again</a>', status_code=400)
    if len(new_password) < 8:
        return HTMLResponse('New password must be at least 8 characters. <a href="/change-password">Try again</a>', status_code=400)
    if not verify_password(current_password, user["password_hash"]):
        return HTMLResponse('Current password is incorrect. <a href="/change-password">Try again</a>', status_code=400)

    conn = db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user["id"]))
    conn.commit()
    conn.close()
    audit(user["username"], "change_password", "user", str(user["id"]))
    return RedirectResponse("/dashboard", status_code=302)


# -----------------------------------------------------------------------------
# User management
# -----------------------------------------------------------------------------

@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    user = require_roles(request, ["admin"])
    csrf_token = ensure_csrf_token(request)

    conn = db()
    rows = conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()
    conn.close()

    table = """
    <table>
        <tr><th>ID</th><th>Username</th><th>Role</th><th>Active</th><th>Update</th><th>Reset Password</th></tr>
    """
    for r in rows:
        active_checked = "checked" if r["active"] == 1 else ""
        table += f"""
        <tr>
            <td>{r['id']}</td>
            <td>{escape(r['username'])}</td>
            <td>{escape(r['role'])}</td>
            <td>{'Yes' if r['active'] == 1 else 'No'}</td>
            <td>
                <form method="post" action="/users/update">
                    {csrf_input(csrf_token)}
                    <input type="hidden" name="user_id" value="{r['id']}">
                    <select name="role">
                        <option value="admin" {selected_attr(r['role'], 'admin')}>Admin</option>
                        <option value="user" {selected_attr(r['role'], 'user')}>User</option>
                        <option value="quality_reviewer" {selected_attr(r['role'], 'quality_reviewer')}>Quality Reviewer</option>
                    </select>
                    <label><input type="checkbox" name="active" value="1" {active_checked}> Active</label>
                    <button type="submit">Update</button>
                </form>
            </td>
            <td>
                <form method="post" action="/users/reset-password">
                    {csrf_input(csrf_token)}
                    <input type="hidden" name="user_id" value="{r['id']}">
                    <input type="password" name="new_password" placeholder="New password" required>
                    <button type="submit">Reset</button>
                </form>
            </td>
        </tr>
        """
    table += "</table>"

    body = f"""
    <h1>User Management</h1>
    <p class="subtitle">Admin-only user and role management. Delete is disabled by design.</p>
    <div class="card">
        <h3>Add User</h3>
        <form method="post" action="/users/add">
            {csrf_input(csrf_token)}
            <label>Username</label><input name="username" required>
            <label>Password</label><input type="password" name="password" required>
            <label>Role</label>
            <select name="role"><option value="user">User</option><option value="quality_reviewer">Quality Reviewer</option><option value="admin">Admin</option></select>
            <button type="submit">Add User</button>
        </form>
    </div>
    <div class="card">{table}</div>
    """
    return html_response_with_csrf(request, layout("Users", user, body))


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
        return HTMLResponse('Password must be at least 8 characters. <a href="/users">Back</a>', status_code=400)

    conn = db()
    try:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (username.strip(), hash_password(password), role, int(time.time())),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return HTMLResponse('Username already exists. <a href="/users">Back</a>', status_code=400)
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
        return HTMLResponse('Password must be at least 8 characters. <a href="/users">Back</a>', status_code=400)
    conn = db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user_id))
    conn.commit()
    conn.close()
    audit(admin["username"], "reset_password", "user", str(user_id))
    return RedirectResponse("/users", status_code=302)


# -----------------------------------------------------------------------------
# Prompt version history
# -----------------------------------------------------------------------------

@app.get("/prompts", response_class=HTMLResponse)
def prompts_page(request: Request):
    user = require_roles(request, ["admin"])
    csrf_token = ensure_csrf_token(request)
    conn = db()
    rows = conn.execute("SELECT * FROM prompt_versions ORDER BY id DESC").fetchall()
    conn.close()

    table = """
    <table>
        <tr><th>ID</th><th>Name</th><th>Active</th><th>Created By</th><th>Created At</th><th>Action</th></tr>
    """
    for r in rows:
        table += f"""
        <tr>
            <td>{r['id']}</td>
            <td>{escape(r['name'] or '')}</td>
            <td>{'Yes' if r['active'] == 1 else 'No'}</td>
            <td>{escape(r['created_by'] or '')}</td>
            <td>{escape(human_time(r['created_at']))}</td>
            <td>
                <form method="post" action="/prompts/activate">
                    {csrf_input(csrf_token)}
                    <input type="hidden" name="prompt_id" value="{r['id']}">
                    <button type="submit">Set Active</button>
                </form>
            </td>
        </tr>
        """
    table += "</table>"

    current_greeting = get_setting("ai_greeting", "")
    current_prompt = get_setting("system_prompt", "")

    body = f"""
    <h1>Prompt Version History</h1>
    <p class="subtitle">Admin-only prompt and greeting version control</p>
    <div class="card">
        <h3>Create Prompt Version</h3>
        <form method="post" action="/prompts/add">
            {csrf_input(csrf_token)}
            <label>Name</label><input name="name" placeholder="Example: Production prompt v1" required>
            <label>Greeting</label><textarea name="greeting" required>{escape(current_greeting)}</textarea>
            <label>System Prompt</label><textarea name="system_prompt" required>{escape(current_prompt)}</textarea>
            <button type="submit">Save Version</button>
        </form>
    </div>
    <div class="card">{table}</div>
    """
    return html_response_with_csrf(request, layout("Prompts", user, body))


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
