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

APP_SECRET = os.getenv("DASHBOARD_SECRET", "change-this-dashboard-secret")
DB_PATH = "/opt/ai-support-agent/data/dashboard.db"
RECORDING_DIR = "/var/spool/asterisk/monitor/ai-support"

app = FastAPI(title="AI IT Support Dashboard")


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
        exists = conn.execute(
            "SELECT id FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if not exists:
            conn.execute(
                """
                INSERT INTO users(username, password_hash, role, active, created_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (username, hash_password(password), role, int(time.time()))
            )

    default_settings = {
        "ai_greeting": "Hi, I am Arif from National Finance IT Support team. Please say Arabic or English to continue.",
        "max_concurrent_calls": "5",
        "recording_retention_days": "30",
        "zammad_enabled": "true"
    }

    for key, value in default_settings.items():
        exists = conn.execute(
            "SELECT key FROM settings WHERE key=?",
            (key,)
        ).fetchone()

        if not exists:
            conn.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, int(time.time()))
            )

    conn.commit()
    conn.close()


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
    nav = ""

    if user:
        nav_links = """
            <a href="/dashboard">Dashboard</a>
        """

        if role in ["admin", "user"]:
            nav_links += """
            <a href="/calls">Calls</a>
            """

        if role in ["admin", "quality_reviewer"]:
            nav_links += """
            <a href="/recordings">Recordings</a>
            """

        if role == "admin":
            nav_links += """
            <a href="/settings">Settings</a>
            """

        nav = f"""
        <div class="nav">
            <div class="brand">
                <div class="brand-mark">NF</div>
                <div>
                    <div class="brand-title">National Finance Oman</div>
                    <div class="brand-subtitle">AI IT Support Dashboard</div>
                </div>
            </div>

            <div class="nav-links">
                {nav_links}
            </div>

            <div class="nav-user">
                <span>{escape(user["username"])} ({escape(role)})</span>
                <a href="/logout" class="logout">Logout</a>
            </div>
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
                min-width: 300px;
            }}

            .brand-mark {{
                width: 42px;
                height: 42px;
                border-radius: 12px;
                background: white;
                color: var(--nf-navy);
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: 800;
                font-size: 15px;
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

            .card h3 {{
                margin-top: 0;
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
                min-height: 80px;
            }}

            button {{
                background: var(--nf-navy);
                color: white;
                border: none;
                padding: 10px 18px;
                border-radius: 8px;
                cursor: pointer;
                font-weight: 700;
            }}

            button:hover {{
                background: var(--nf-blue);
            }}

            .btn-link {{
                display: inline-block;
                background: var(--nf-navy);
                color: white;
                text-decoration: none;
                padding: 7px 12px;
                border-radius: 8px;
                font-weight: 700;
                font-size: 13px;
            }}

            .btn-link:hover {{
                background: var(--nf-blue);
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

            .login-wrap {{
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                background: radial-gradient(circle at center, #2B4A9F 0%, #132250 75%);
                padding: 20px;
            }}

            .login-card {{
                max-width: 420px;
                width: 100%;
                background: white;
                border-radius: 18px;
                padding: 34px;
                box-shadow: 0 24px 60px rgba(0, 0, 0, 0.30);
                border-top: 5px solid var(--nf-red);
            }}

            .login-card h2 {{
                margin: 0 0 8px;
                color: var(--nf-navy);
                font-size: 25px;
            }}

            .login-card p {{
                margin-top: 0;
                color: var(--nf-muted);
                font-size: 14px;
            }}

            .login-card label {{
                color: var(--nf-navy);
                font-weight: 700;
                font-size: 12px;
                text-transform: uppercase;
            }}

            .login-card input {{
                background: #ffffff;
                border: 1.5px solid #b8c4e3;
                min-height: 42px;
            }}

            .login-card button {{
                width: 100%;
                min-height: 44px;
                margin-top: 10px;
            }}

            @media (max-width: 900px) {{
                .grid {{
                    grid-template-columns: repeat(2, 1fr);
                }}

                .nav {{
                    flex-wrap: wrap;
                }}

                .brand {{
                    min-width: auto;
                }}
            }}

            @media (max-width: 600px) {{
                .grid {{
                    grid-template-columns: 1fr;
                }}

                .container {{
                    padding: 18px;
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
    body = """
    <div class="login-wrap">
        <div class="login-card">
            <h2>AI IT Support Dashboard</h2>
            <p>National Finance Oman — Secure Operations Portal</p>

            <form method="post" action="/login">
                <label>Username</label>
                <input name="username" required>

                <label>Password</label>
                <input name="password" type="password" required>

                <button type="submit">Login</button>
            </form>
        </div>
    </div>
    """

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
            }}

            .login-card {{
                max-width: 420px;
                width: 100%;
                background: white;
                border-radius: 18px;
                padding: 34px;
                box-shadow: 0 24px 60px rgba(0, 0, 0, 0.30);
                border-top: 5px solid var(--nf-red);
            }}

            h2 {{
                margin: 0 0 8px;
                color: var(--nf-navy);
                font-size: 25px;
            }}

            p {{
                margin-top: 0;
                color: var(--nf-muted);
                font-size: 14px;
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
        </style>
    </head>
    <body>
        {body}
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
            "Invalid login. <a href='/login'>Try again</a>",
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

    conn = db()
    total_calls = conn.execute("SELECT COUNT(*) c FROM calls").fetchone()["c"]
    tickets = conn.execute("SELECT COUNT(*) c FROM calls WHERE ticket_created=1").fetchone()["c"]
    failed = conn.execute("SELECT COUNT(*) c FROM calls WHERE status='failed'").fetchone()["c"]
    verified = conn.execute(
        "SELECT COUNT(*) c FROM calls WHERE verified_name IS NOT NULL AND verified_name != ''"
    ).fetchone()["c"]
    conn.close()

    if user["role"] == "admin":
        recordings_count = len(recording_files())

        metrics = f"""
        <div class="metric"><h2>{total_calls}</h2><p>Total Calls</p></div>
        <div class="metric"><h2>{recordings_count}</h2><p>Recordings</p></div>
        <div class="metric"><h2>{tickets}</h2><p>Tickets Created</p></div>
        <div class="metric"><h2>{verified}</h2><p>Verified Callers</p></div>
        """
    else:
        metrics = f"""
        <div class="metric"><h2>{total_calls}</h2><p>Total Calls</p></div>
        <div class="metric"><h2>{tickets}</h2><p>Tickets Created</p></div>
        <div class="metric"><h2>{verified}</h2><p>Verified Callers</p></div>
        <div class="metric"><h2>{failed}</h2><p>Failed Calls</p></div>
        """

    body = f"""
    <h1>AI IT Support Dashboard</h1>
    <p class="subtitle">National Finance Oman — Voice AI Support Operations</p>

    <div class="grid">
        {metrics}
    </div>

    <div class="card">
        <h3>System Overview</h3>
        <ul>
            <li>AI voice agent is available for IT support calls.</li>
            <li>Zammad tickets are created for unresolved incidents.</li>
            <li>Call metadata is logged for operational visibility.</li>
            <li>No delete actions are available in this dashboard.</li>
        </ul>
    </div>
    """

    return layout("Dashboard", user, body)


@app.get("/calls", response_class=HTMLResponse)
def calls(request: Request):
    user = require_roles(request, ["admin", "user"])

    conn = db()
    rows = conn.execute(
        "SELECT * FROM calls ORDER BY id DESC LIMIT 200"
    ).fetchall()
    conn.close()

    show_recording = user["role"] == "admin"

    if show_recording:
        table = """
        <table>
            <tr>
                <th>ID</th>
                <th>Caller</th>
                <th>Employee</th>
                <th>Name</th>
                <th>Language</th>
                <th>Duration</th>
                <th>Status</th>
                <th>Ticket</th>
                <th>Recording</th>
            </tr>
        """
    else:
        table = """
        <table>
            <tr>
                <th>ID</th>
                <th>Caller</th>
                <th>Employee</th>
                <th>Name</th>
                <th>Language</th>
                <th>Duration</th>
                <th>Status</th>
                <th>Ticket</th>
            </tr>
        """

    for r in rows:
        status = escape(str(r["status"] or ""))
        ticket = escape(str(r["ticket_number"] or ""))

        status_html = f"<span class='status-pill'>{status}</span>" if status else ""
        ticket_html = f"<span class='ticket-pill'>{ticket}</span>" if ticket else ""

        if show_recording:
            rec = r["recording_file"] or ""
            rec_name = Path(rec).name if rec else ""
            rec_link = ""

            if rec_name:
                rec_link = f"<a class='btn-link' href='/recordings/play?file={quote(rec_name)}'>Play</a>"

            table += f"""
            <tr>
                <td>{r["id"]}</td>
                <td>{escape(str(r["caller_number"] or ""))}</td>
                <td>{escape(str(r["employee_id"] or ""))}</td>
                <td>{escape(str(r["verified_name"] or ""))}</td>
                <td>{escape(str(r["language"] or ""))}</td>
                <td>{escape(str(r["duration_seconds"] or ""))}</td>
                <td>{status_html}</td>
                <td>{ticket_html}</td>
                <td>{rec_link}</td>
            </tr>
            """
        else:
            table += f"""
            <tr>
                <td>{r["id"]}</td>
                <td>{escape(str(r["caller_number"] or ""))}</td>
                <td>{escape(str(r["employee_id"] or ""))}</td>
                <td>{escape(str(r["verified_name"] or ""))}</td>
                <td>{escape(str(r["language"] or ""))}</td>
                <td>{escape(str(r["duration_seconds"] or ""))}</td>
                <td>{status_html}</td>
                <td>{ticket_html}</td>
            </tr>
            """

    table += "</table>"

    body = f"""
    <h1>Call History</h1>
    <p class="subtitle">Recent AI support interactions</p>

    <div class="card">
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

    conn = db()
    rows = conn.execute("SELECT * FROM settings ORDER BY key").fetchall()
    conn.close()

    form = """
    <form method="post" action="/settings">
    """

    for r in rows:
        form += f"""
        <label>{escape(r["key"])}</label>
        <textarea name="{escape(r["key"])}">{escape(r["value"] or "")}</textarea>
        """

    form += """
        <button type="submit">Save Settings</button>
    </form>
    """

    body = f"""
    <h1>Settings</h1>
    <p class="subtitle">Dashboard and AI support configuration</p>

    <div class="card">
        <p class="small">Phase 1 stores settings here. Bridge integration can read these later.</p>
        {form}
    </div>
    """

    return layout("Settings", user, body)


@app.post("/settings")
async def save_settings(request: Request):
    user = require_roles(request, ["admin"])
    data = await request.form()

    conn = db()

    for key, value in data.items():
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