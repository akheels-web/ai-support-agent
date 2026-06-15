import os
import hmac
import time
import sqlite3
import hashlib
from pathlib import Path
from urllib.parse import quote
from html import escape

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

APP_SECRET = os.getenv("DASHBOARD_SECRET", "change-this-dashboard-secret")
DB_PATH = "/opt/ai-support-agent/data/dashboard.db"
RECORDING_DIR = "/var/spool/asterisk/monitor/ai-support"
BRAND_ASSETS_DIR = "/opt/ai-support-agent/zammad-branding/assets"

NF_LOGO_LOCAL = "/brand-assets/nfc-logo.svg"
TCT_LOGO_LOCAL = "/brand-assets/tct-logo.png"

app = FastAPI(title="AI IT Support Dashboard")

Path(BRAND_ASSETS_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/brand-assets", StaticFiles(directory=BRAND_ASSETS_DIR), name="brand-assets")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password):
    salt = "ai-support-agent"
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt.encode(),
        100000
    ).hex()


def sign_token(value):
    sig = hmac.new(APP_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def verify_token(token):
    try:
        value, sig = token.rsplit(".", 1)
        expected = hmac.new(APP_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return value
    except Exception:
        return None
    return None


def current_user(request: Request):
    token = request.cookies.get("ai_dashboard_token")
    if not token:
        return None

    username = verify_token(token)
    if not username:
        return None

    conn = db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=? AND active=1",
        (username,)
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


def audit(username, action, entity_type="", entity_id=""):
    conn = db()
    conn.execute(
        """
        INSERT INTO audit_logs(username, action, entity_type, entity_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (username, action, entity_type, entity_id, int(time.time()))
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


def init_db():
    Path("/opt/ai-support-agent/data").mkdir(parents=True, exist_ok=True)

    conn = db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password_hash TEXT,
        role TEXT,
        active INTEGER DEFAULT 1,
        created_at INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id TEXT,
        caller_number TEXT,
        called_number TEXT,
        employee_id TEXT,
        verified_name TEXT,
        language TEXT,
        start_time INTEGER,
        end_time INTEGER,
        duration_seconds INTEGER,
        status TEXT,
        ticket_number TEXT,
        ticket_created INTEGER DEFAULT 0,
        recording_file TEXT,
        summary TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS quality_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recording_file TEXT,
        reviewer TEXT,
        rating TEXT,
        notes TEXT,
        created_at INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        action TEXT,
        entity_type TEXT,
        entity_id TEXT,
        created_at INTEGER
    )
    """)

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
                (username, hash_password(password), role, int(time.time()))
            )

    default_settings = {
        "organization_name": "National Finance Oman",
        "dashboard_title": "AI IT Support Dashboard",
        "dashboard_subtitle": "Voice AI Support Operations",
        "ai_greeting": "Hi, I am Arif from National Finance IT Support team. Please say Arabic or English to continue.",
        "system_prompt": "You are Arif, an AI IT Support voice agent for National Finance IT Support team.",
        "max_concurrent_calls": "5",
        "recording_retention_days": "30",
        "zammad_enabled": "true",
        "profile_icon_text": "NF"
    }

    for key, value in default_settings.items():
        exists = conn.execute("SELECT key FROM settings WHERE key=?", (key,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, int(time.time()))
            )

    conn.commit()
    conn.close()


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
        seconds_int = int(seconds)
        mins = seconds_int / 60
        return f"{mins:.1f}"
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

        files.append({
            "name": file.name,
            "path": str(file),
            "caller": caller,
            "size_mb": round(stat.st_size / 1024 / 1024, 2),
            "created_at": stat.st_mtime
        })

    return files


def layout(title, user, body):
    role = user["role"] if user else ""

    organization_name = get_setting("organization_name", "National Finance Oman")
    dashboard_title = get_setting("dashboard_title", "AI IT Support Dashboard")
    profile_icon_text = get_setting("profile_icon_text", "NF")

    nav = ""

    if user:
        nav_links = '<a href="/dashboard">Dashboard</a>'

        if role in ["admin", "user"]:
            nav_links += '<a href="/calls">Calls</a>'

        if role in ["admin", "quality_reviewer"]:
            nav_links += '<a href="/recordings">Recordings</a>'

        if role == "admin":
            nav_links += '<a href="/settings">Settings</a>'

        nav = f"""
        <div class="nav">
            <div class="brand">
                <img class="brand-logo" src="{NF_LOGO_LOCAL}" alt="National Finance Oman">
                <div>
                    <div class="brand-title">{escape(organization_name)}</div>
                    <div class="brand-subtitle">{escape(dashboard_title)}</div>
                </div>
            </div>

            <div class="nav-links">
                {nav_links}
            </div>

            <div class="nav-user">
                <span>{escape(user["username"])} ({escape(role)})</span>
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
                --nf-warning: #F59E0B;
                --nf-danger: #C8102E;
            }}

            * {{
                box-sizing: border-box;
            }}

            body {{
                font-family: "Segoe UI", Arial, sans-serif;
                margin: 0;
                background: var(--nf-bg);
                color: var(--nf-text);
            }}

            .nav {{
                background: linear-gradient(90deg, var(--nf-navy-dark) 0%, var(--nf-navy) 75%);
                color: white;
                padding: 14px 24px;
                display: flex;
                align-items: center;
                gap: 28px;
                box-shadow: 0 2px 14px rgba(0, 0, 0, 0.18);
                position: sticky;
                top: 0;
                z-index: 10;
            }}

            .brand {{
                display: flex;
                align-items: center;
                gap: 12px;
                min-width: 320px;
            }}

            .brand-logo {{
                height: 42px;
                max-width: 150px;
                background: white;
                border-radius: 10px;
                padding: 6px 10px;
                object-fit: contain;
                border-top: 4px solid var(--nf-red);
            }}

            .brand-title {{
                font-size: 15px;
                font-weight: 800;
                letter-spacing: 0.2px;
            }}

            .brand-subtitle {{
                font-size: 11px;
                opacity: 0.78;
                margin-top: 2px;
            }}

            .nav-links {{
                display: flex;
                align-items: center;
                gap: 18px;
                flex: 1;
            }}

            .nav a {{
                color: white;
                text-decoration: none;
                font-weight: 600;
                font-size: 14px;
                opacity: 0.9;
            }}

            .nav a:hover {{
                opacity: 1;
                color: #dce7ff;
            }}

            .nav-user {{
                display: flex;
                align-items: center;
                gap: 14px;
                font-size: 13px;
            }}

            .logout {{
                background: rgba(255, 255, 255, 0.12);
                padding: 8px 12px;
                border-radius: 8px;
            }}

            .container {{
                padding: 28px;
                max-width: 1500px;
                margin: 0 auto;
                min-height: calc(100vh - 130px);
            }}

            h1 {{
                margin: 0 0 4px;
                color: var(--nf-navy);
                font-size: 28px;
                font-weight: 800;
            }}

            h2, h3 {{
                color: var(--nf-navy);
            }}

            .subtitle {{
                margin: 0 0 22px;
                color: var(--nf-muted);
                font-size: 14px;
            }}

            .grid {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 18px;
                margin-bottom: 22px;
            }}

            .metric {{
                background: var(--nf-card);
                border-radius: 16px;
                padding: 22px;
                box-shadow: 0 4px 16px rgba(27, 47, 107, 0.10);
                border: 1px solid var(--nf-border);
                border-top: 4px solid var(--nf-red);
            }}

            .metric h2 {{
                margin: 0;
                font-size: 34px;
                color: var(--nf-navy);
                font-weight: 800;
            }}

            .metric p {{
                margin: 8px 0 0;
                color: var(--nf-muted);
                font-weight: 600;
                font-size: 14px;
            }}

            .card {{
                background: var(--nf-card);
                border-radius: 16px;
                padding: 22px;
                margin-bottom: 22px;
                box-shadow: 0 4px 16px rgba(27, 47, 107, 0.10);
                border: 1px solid var(--nf-border);
            }}

            .filter-box {{
                display: grid;
                grid-template-columns: 2fr 1fr 1fr 1fr 1fr;
                gap: 12px;
                align-items: end;
                margin-bottom: 12px;
            }}

            .filter-actions {{
                display: flex;
                gap: 10px;
                align-items: center;
                margin-bottom: 18px;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                border-radius: 12px;
                overflow: hidden;
            }}

            th, td {{
                padding: 12px;
                border-bottom: 1px solid #e5e7eb;
                text-align: left;
                font-size: 14px;
                vertical-align: top;
            }}

            th {{
                background: var(--nf-navy);
                color: white;
                font-weight: 700;
                white-space: nowrap;
            }}

            tr:hover td {{
                background: #E8EDF8;
            }}

            .status-pill {{
                display: inline-block;
                padding: 4px 10px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 700;
                background: #E8EDF8;
                color: var(--nf-navy);
            }}

            .ticket-pill {{
                display: inline-block;
                padding: 4px 10px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 800;
                background: #FEE2E2;
                color: var(--nf-red);
            }}

            input, textarea, select {{
                width: 100%;
                padding: 10px;
                margin: 6px 0 12px;
                border: 1px solid var(--nf-border);
                border-radius: 8px;
                font-family: inherit;
            }}

            textarea {{
                min-height: 95px;
            }}

            label {{
                font-weight: 700;
                color: var(--nf-navy);
                font-size: 13px;
            }}

            button {{
                background: var(--nf-navy);
                color: white;
                border: none;
                padding: 10px 18px;
                border-radius: 8px;
                cursor: pointer;
                font-weight: 700;
                height: 40px;
            }}

            button:hover {{
                background: var(--nf-blue);
            }}

            .btn-link {{
                display: inline-flex;
                align-items: center;
                height: 40px;
                background: #E8EDF8;
                color: var(--nf-navy);
                text-decoration: none;
                padding: 0 14px;
                border-radius: 8px;
                font-weight: 700;
                font-size: 13px;
            }}

            .btn-link:hover {{
                background: #D1D9F0;
            }}

            .danger-note {{
                color: var(--nf-danger);
                font-weight: bold;
            }}

            .small {{
                color: var(--nf-muted);
                font-size: 13px;
            }}

            audio {{
                width: 300px;
            }}

            .footer-credit {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 10px;
                color: var(--nf-muted);
                font-size: 12px;
                padding: 18px;
            }}

            .footer-credit img {{
                height: 28px;
                max-width: 130px;
                object-fit: contain;
            }}

            @media (max-width: 1100px) {{
                .grid {{
                    grid-template-columns: repeat(2, 1fr);
                }}

                .filter-box {{
                    grid-template-columns: 1fr 1fr;
                }}
            }}

            @media (max-width: 700px) {{
                .grid {{
                    grid-template-columns: 1fr;
                }}

                .container {{
                    padding: 18px;
                }}

                .nav {{
                    flex-wrap: wrap;
                }}

                .brand {{
                    min-width: auto;
                }}

                .filter-box {{
                    grid-template-columns: 1fr;
                }}

                table {{
                    display: block;
                    overflow-x: auto;
                }}
            }}
        </style>
    </head>

    <body>
        {nav}
        <div class="container">
            {body}
        </div>
        {footer}
    </body>
    </html>
    """


@app.on_event("startup")
def startup():
    init_db()


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

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Login</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">

        <style>
            :root {{
                --nf-navy: #1B2F6B;
                --nf-blue: #2B4A9F;
                --nf-red: #C8102E;
                --nf-muted: #6B7280;
            }}

            * {{
                box-sizing: border-box;
            }}

            body {{
                margin: 0;
                font-family: "Segoe UI", Arial, sans-serif;
            }}

            .login-wrap {{
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                background: radial-gradient(circle at center, #2B4A9F 0%, #132250 75%);
                padding: 20px;
                position: relative;
            }}

            .login-card {{
                max-width: 430px;
                width: 100%;
                background: white;
                border-radius: 18px;
                padding: 34px;
                box-shadow: 0 24px 60px rgba(0, 0, 0, 0.30);
                border-top: 5px solid var(--nf-red);
            }}

            .nf-logo {{
                display: block;
                max-width: 235px;
                max-height: 85px;
                margin: 0 auto 22px;
                object-fit: contain;
            }}

            h2 {{
                margin: 0 0 8px;
                color: var(--nf-navy);
                font-size: 25px;
                text-align: center;
            }}

            p {{
                margin-top: 0;
                color: var(--nf-muted);
                font-size: 14px;
                text-align: center;
            }}

            label {{
                color: var(--nf-navy);
                font-weight: 700;
                font-size: 12px;
                text-transform: uppercase;
                display: block;
                margin-bottom: 6px;
            }}

            input {{
                width: 100%;
                padding: 10px;
                margin-bottom: 14px;
                border: 1.5px solid #b8c4e3;
                border-radius: 8px;
                min-height: 42px;
                font-size: 14px;
            }}

            input:focus {{
                border-color: var(--nf-blue);
                box-shadow: 0 0 0 3px rgba(43, 74, 159, 0.15);
                outline: none;
            }}

            button {{
                width: 100%;
                min-height: 44px;
                margin-top: 10px;
                background: var(--nf-navy);
                color: white;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                font-weight: 700;
                font-size: 14px;
            }}

            button:hover {{
                background: var(--nf-blue);
            }}

            .login-footer {{
                position: fixed;
                left: 0;
                right: 0;
                bottom: 22px;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 10px;
                color: rgba(255, 255, 255, 0.78);
                font-size: 12px;
            }}

            .login-footer img {{
                height: 32px;
                max-width: 140px;
                object-fit: contain;
                background: rgba(255,255,255,0.08);
                border-radius: 6px;
                padding: 3px 6px;
            }}
        </style>
    </head>

    <body>
        <div class="login-wrap">
            <div class="login-card">
                <img class="nf-logo" src="{NF_LOGO_LOCAL}" alt="National Finance Oman">

                <h2>{escape(dashboard_title)}</h2>
                <p>{escape(organization_name)} — Secure Operations Portal</p>

                <form method="post" action="/login">
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


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    conn = db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=? AND active=1",
        (username,)
    ).fetchone()
    conn.close()

    if not user or user["password_hash"] != hash_password(password):
        return HTMLResponse(
            'Invalid login. <a href="/login">Try again</a>',
            status_code=401
        )

    token = sign_token(username)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("ai_dashboard_token", token, httponly=True, max_age=28800)

    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("ai_dashboard_token")
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_roles(request, ["admin", "user"])

    organization_name = get_setting("organization_name", "National Finance Oman")
    dashboard_title = get_setting("dashboard_title", "AI IT Support Dashboard")
    dashboard_subtitle = get_setting("dashboard_subtitle", "Voice AI Support Operations")

    now = int(time.time())
    live_cutoff = now - 600

    conn = db()
    total_calls = conn.execute("SELECT COUNT(*) c FROM calls").fetchone()["c"]
    tickets = conn.execute("SELECT COUNT(*) c FROM calls WHERE ticket_created=1").fetchone()["c"]
    verified = conn.execute(
        "SELECT COUNT(*) c FROM calls WHERE verified_name IS NOT NULL AND verified_name != ''"
    ).fetchone()["c"]
    failed = conn.execute(
        "SELECT COUNT(*) c FROM calls WHERE status IN ('failed', 'openai_connection_failed', 'openai_response_failed', 'ticket_failed')"
    ).fetchone()["c"]
    ongoing = conn.execute(
        """
        SELECT COUNT(*) c FROM calls
        WHERE end_time IS NULL
        AND start_time >= ?
        AND status IN ('in_progress', 'language_selected', 'verified', 'troubleshooting')
        """,
        (live_cutoff,)
    ).fetchone()["c"]
    rejected = conn.execute(
        "SELECT COUNT(*) c FROM calls WHERE status IN ('rejected', 'max_concurrent_rejected')"
    ).fetchone()["c"]
    conn.close()

    if user["role"] == "admin":
        recordings_count = len(recording_files())

        metrics = f"""
        <div class="metric"><h2>{total_calls}</h2><p>Total Calls</p></div>
        <div class="metric"><h2>{ongoing}</h2><p>Ongoing Calls</p></div>
        <div class="metric"><h2>{tickets}</h2><p>Tickets Created</p></div>
        <div class="metric"><h2>{recordings_count}</h2><p>Recordings</p></div>
        <div class="metric"><h2>{verified}</h2><p>Verified Callers</p></div>
        <div class="metric"><h2>0</h2><p>Calls in Queue</p></div>
        <div class="metric"><h2>{rejected}</h2><p>Rejected Calls</p></div>
        <div class="metric"><h2>{failed}</h2><p>Failed Calls</p></div>
        """
    else:
        metrics = f"""
        <div class="metric"><h2>{total_calls}</h2><p>Total Calls</p></div>
        <div class="metric"><h2>{ongoing}</h2><p>Ongoing Calls</p></div>
        <div class="metric"><h2>{tickets}</h2><p>Tickets Created</p></div>
        <div class="metric"><h2>{verified}</h2><p>Verified Callers</p></div>
        <div class="metric"><h2>0</h2><p>Calls in Queue</p></div>
        <div class="metric"><h2>{rejected}</h2><p>Rejected Calls</p></div>
        <div class="metric"><h2>{failed}</h2><p>Failed Calls</p></div>
        <div class="metric"><h2>{get_setting("max_concurrent_calls", "5")}</h2><p>Max Concurrent Calls</p></div>
        """

    body = f"""
    <h1>{escape(dashboard_title)}</h1>
    <p class="subtitle">{escape(organization_name)} — {escape(dashboard_subtitle)}</p>

    <div class="grid">
        {metrics}
    </div>

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
            caller_number LIKE ?
            OR verified_name LIKE ?
            OR employee_id LIKE ?
            OR ticket_number LIKE ?
            OR status LIKE ?
            OR summary LIKE ?
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
    status_rows = conn.execute(
        "SELECT DISTINCT status FROM calls WHERE status IS NOT NULL AND status != '' ORDER BY status"
    ).fetchall()
    conn.close()

    status_options = '<option value="">All Status</option>'
    for s in status_rows:
        selected = "selected" if status == s["status"] else ""
        status_options += f'<option value="{escape(s["status"])}" {selected}>{escape(s["status"])}</option>'

    language_options = f"""
    <option value="">All Languages</option>
    <option value="en" {"selected" if language == "en" else ""}>English</option>
    <option value="ar" {"selected" if language == "ar" else ""}>Arabic</option>
    """

    ticket_options = f"""
    <option value="">All Tickets</option>
    <option value="1" {"selected" if ticket_created == "1" else ""}>Ticket Created</option>
    <option value="0" {"selected" if ticket_created == "0" else ""}>No Ticket</option>
    """

    table = """
    <table>
        <tr>
            <th>ID</th>
            <th>Caller ID</th>
            <th>Employee</th>
            <th>Caller Name</th>
            <th>Language</th>
            <th>Duration(mins)</th>
            <th>Status</th>
            <th>Ticket</th>
            <th>Summary</th>
        </tr>
    """

    for r in rows:
        caller_id = display_caller_id(r)
        caller_name = display_caller_name(r)
        duration_mins = format_minutes(r["duration_seconds"])
        status_text = str(r["status"] or "")
        ticket = str(r["ticket_number"] or "")
        summary = str(r["summary"] or "")

        status_html = f"<span class='status-pill'>{escape(status_text)}</span>" if status_text else ""
        ticket_html = f"<span class='ticket-pill'>{escape(ticket)}</span>" if ticket else ""

        table += f"""
        <tr>
            <td>{r["id"]}</td>
            <td>{escape(str(caller_id or ""))}</td>
            <td>{escape(str(r["employee_id"] or ""))}</td>
            <td>{escape(caller_name)}</td>
            <td>{escape(str(r["language"] or ""))}</td>
            <td>{escape(duration_mins)}</td>
            <td>{status_html}</td>
            <td>{ticket_html}</td>
            <td>{escape(summary[:160])}</td>
        </tr>
        """

    table += "</table>"

    body = f"""
    <h1>Call History</h1>
    <p class="subtitle">Recent AI support interactions with search and filters</p>

    <div class="card">
        <form method="get" action="/calls">
            <div class="filter-box">
                <div>
                    <label>Search</label>
                    <input name="q" value="{escape(q)}" placeholder="Caller, employee, ticket, status, summary">
                </div>

                <div>
                    <label>Employee ID</label>
                    <input name="employee_id" value="{escape(employee_id)}" placeholder="1002">
                </div>

                <div>
                    <label>Status</label>
                    <select name="status">{status_options}</select>
                </div>

                <div>
                    <label>Language</label>
                    <select name="language">{language_options}</select>
                </div>

                <div>
                    <label>Ticket</label>
                    <select name="ticket_created">{ticket_options}</select>
                </div>
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


@app.get("/recordings", response_class=HTMLResponse)
def recordings(request: Request):
    user = require_roles(request, ["admin", "quality_reviewer"])
    files = recording_files()

    table = """
    <table>
        <tr>
            <th>File</th>
            <th>Caller ID</th>
            <th>Size MB</th>
            <th>Playback</th>
            <th>Review</th>
        </tr>
    """

    for f in files:
        encoded = quote(f["name"])

        table += f"""
        <tr>
            <td>{escape(f["name"])}</td>
            <td>{escape(f["caller"])}</td>
            <td>{f["size_mb"]}</td>
            <td>
                <audio controls>
                    <source src="/recordings/play?file={encoded}" type="audio/wav">
                </audio>
            </td>
            <td>
                <form method="post" action="/quality/review">
                    <input type="hidden" name="recording_file" value="{escape(f["name"])}">
                    <select name="rating">
                        <option>Good</option>
                        <option>Needs Improvement</option>
                        <option>Escalated</option>
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

    return layout("Recordings", user, body)


@app.get("/recordings/play")
def play_recording(request: Request, file: str):
    user = require_roles(request, ["admin", "quality_reviewer"])

    safe_name = Path(file).name
    full_path = Path(RECORDING_DIR) / safe_name

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Recording not found")

    audit(user["username"], "play_recording", "recording", safe_name)

    return FileResponse(
        path=str(full_path),
        media_type="audio/wav",
        filename=safe_name
    )


@app.post("/quality/review")
def save_review(
    request: Request,
    recording_file: str = Form(...),
    rating: str = Form(...),
    notes: str = Form("")
):
    user = require_roles(request, ["admin", "quality_reviewer"])

    conn = db()
    conn.execute(
        """
        INSERT INTO quality_reviews(recording_file, reviewer, rating, notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (Path(recording_file).name, user["username"], rating, notes, int(time.time()))
    )
    conn.commit()
    conn.close()

    audit(user["username"], "save_quality_review", "recording", recording_file)

    return RedirectResponse("/recordings", status_code=302)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    user = require_roles(request, ["admin"])

    label_map = {
        "organization_name": "Organization Name",
        "dashboard_title": "Dashboard Title",
        "dashboard_subtitle": "Dashboard Subtitle",
        "profile_icon_text": "Profile Icon Text",
        "ai_greeting": "AI Greeting",
        "system_prompt": "System Prompt",
        "max_concurrent_calls": "Max Concurrent Calls",
        "recording_retention_days": "Recording Retention Days",
        "zammad_enabled": "Zammad Enabled"
    }

    editable_keys = [
        "organization_name",
        "dashboard_title",
        "dashboard_subtitle",
        "profile_icon_text",
        "ai_greeting",
        "system_prompt",
        "max_concurrent_calls",
        "recording_retention_days",
        "zammad_enabled",
    ]

    form = '<form method="post" action="/settings">'

    for key in editable_keys:
        value = get_setting(key, "")
        label = label_map.get(key, key)

        form += f"""
        <label>{escape(label)}</label>
        <textarea name="{escape(key)}">{escape(value or "")}</textarea>
        """

    form += """
        <button type="submit">Save Settings</button>
    </form>
    """

    body = f"""
    <h1>Settings</h1>
    <p class="subtitle">Admin-only configuration for dashboard, prompts, and AI behavior</p>

    <div class="card">
        <p class="small">
            Logo settings are fixed by system configuration and are not editable from the dashboard.
            Prompt and greeting values can be connected to the bridge in the next phase.
        </p>
        {form}
    </div>
    """

    return layout("Settings", user, body)


@app.post("/settings")
async def save_settings(request: Request):
    user = require_roles(request, ["admin"])
    data = await request.form()

    allowed_keys = {
        "organization_name",
        "dashboard_title",
        "dashboard_subtitle",
        "profile_icon_text",
        "ai_greeting",
        "system_prompt",
        "max_concurrent_calls",
        "recording_retention_days",
        "zammad_enabled",
    }

    conn = db()

    for key, value in data.items():
        if key not in allowed_keys:
            continue

        conn.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, int(time.time()))
        )

    conn.commit()
    conn.close()

    audit(user["username"], "update_settings", "settings", "multiple")

    return RedirectResponse("/settings", status_code=302)