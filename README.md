# AI IT Support Voice Agent

An AI voice agent ("Arif") that answers IT support calls for National Finance,
verifies the caller, troubleshoots, creates Zammad tickets, and can transfer to
a human. English and Arabic. A FastAPI dashboard shows calls, recordings, and
security events.

## Architecture

```text
Caller → Asterisk PBX → WebSocket bridge (app/openai_realtime_bridge.py)
             → OpenAI Realtime API (voice agent "Arif")
             → verify.py (users.csv)  →  zammad_api.py (tickets)
             → transfer.py (Asterisk AMI, optional)
             → SQLite (data/dashboard.db)  →  FastAPI dashboard/app.py
```

| Component | File | Purpose |
|---|---|---|
| Voice bridge | `app/openai_realtime_bridge.py` | Asterisk ↔ OpenAI audio, call flow, tools |
| Verification | `app/verify.py` | Match caller name + employee ID against `users.csv` |
| Ticketing | `app/zammad_api.py` | Create Zammad tickets |
| Transfer | `app/transfer.py` | Redirect a live call to a human via AMI |
| Call log | `app/call_logger.py` | Persist call metadata to SQLite |
| Abuse protection | `app/security_guard.py` | Rate limits + lockouts (calls, verification, login) |
| Dashboard | `dashboard/app.py` | Ops UI: calls, recordings, users, security events |

## Quickstart

Runs from `/opt/ai-support-agent`. Requires Python 3.10+, an Asterisk PBX, a
Zammad instance, and an OpenAI API key.

```bash
python3 -m venv venv && source venv/bin/activate
pip install fastapi uvicorn websockets python-dotenv requests python-multipart jinja2

cp .env.example .env      # then edit: OPENAI_API_KEY, ZAMMAD_TOKEN, DASHBOARD_SECRET
openssl rand -hex 32      # use for DASHBOARD_SECRET

# Caller verification data
nano data/users.csv       # columns: employee_id,name,aliases,email,phone,department,vip

# Run the voice bridge (listens on 127.0.0.1:8765)
PYTHONPATH=. python app/openai_realtime_bridge.py

# Run the dashboard (separate process)
uvicorn dashboard.app:app --host 0.0.0.0 --port 8090
```

Default dashboard logins are created on first run (`admin`/`admin123`,
`user`/`user123`, `reviewer`/`reviewer123`) — **change these before any
non-demo use.**

## Documentation

- `DEPLOYMENT.md` — full install, systemd units, Asterisk config, troubleshooting.
- `PRODUCTION_DEPLOYMENT_GUIDE.md` — production hardening, PostgreSQL, Webex Calling design.
- Section 15 below — security and abuse protection.

---

## 15. Security and Abuse Protection

This project includes additional security and anti-abuse controls to protect both the voice agent and the dashboard.

### 15.1 Asterisk / AI Voice Call Rate Limiting

The bridge can rate-limit repeated calls from the same caller number to reduce spam, bot calls, and denial-of-service attempts.

Recommended `.env` values:

```ini
CALLS_PER_NUMBER_LIMIT=5
CALLS_PER_NUMBER_WINDOW=600
CALLS_PER_NUMBER_LOCK=900
```

Meaning:

```text
Maximum 5 calls from the same caller number within 10 minutes.
If exceeded, block the caller for 15 minutes.
```

Implementation file:

```text
app/security_guard.py
```

Bridge integration file:

```text
app/openai_realtime_bridge.py
```

Rejected or rate-limited calls should be logged with statuses such as:

```text
rejected
verification_blocked
```

---

### 15.2 Verification Abuse Protection

The system can lock out repeated failed verification attempts to prevent attackers from guessing employee ID and name combinations.

Recommended `.env` values:

```ini
VERIFY_FAIL_LIMIT=5
VERIFY_FAIL_WINDOW=3600
VERIFY_FAIL_LOCK=3600
```

Meaning:

```text
Maximum 5 failed verification attempts per caller or employee ID within 1 hour.
If exceeded, block further verification attempts for 1 hour.
```

Events are logged in the `security_events` table.

Common event types:

```text
verification_failed
verification_blocked
call_rate_limited
```

---

### 15.3 Dashboard Login Rate Limiting

The dashboard login page should rate-limit failed login attempts by client IP address.

Recommended policy:

```text
Maximum 5 failed login attempts within 5 minutes.
Lock login attempts from that IP for 15 minutes.
```

Common event types:

```text
dashboard_login_failed
dashboard_login_rate_limited
dashboard_login_success
```

Implementation file:

```text
app/security_guard.py
```

Dashboard integration file:

```text
dashboard/app.py
```

---

### 15.4 Dashboard Session Security

Dashboard sessions should use server-side session tokens instead of signing only the username.

Required cookie settings:

```python
httponly=True
samesite="strict"
secure=True  # when HTTPS is enabled
max_age=28800
```

Required `.env` values:

```ini
DASHBOARD_SECRET=<long-random-secret>
DASHBOARD_COOKIE_SECURE=false
```

For HTTPS deployments, use:

```ini
DASHBOARD_COOKIE_SECURE=true
```

Generate a strong dashboard secret:

```bash
openssl rand -hex 32
```

---

### 15.5 CSRF Protection for Dashboard Forms

All dashboard POST actions should use CSRF tokens.

High-risk routes that must validate CSRF tokens:

```text
POST /login
POST /settings
POST /users/add
POST /users/update
POST /users/reset-password
POST /change-password
POST /prompts/add
POST /prompts/activate
POST /quality/review
```

The dashboard should generate a CSRF token on GET pages with forms and validate it on all POST requests.

---

### 15.6 Security Events Page

Admins should have access to a security events page:

```text
/security-events
```

This page should show:

- Failed dashboard logins
- Dashboard login lockouts
- Voice-call rate limit events
- Failed verification attempts
- Verification lockouts
- Other abuse-related events

Primary table:

```text
security_events
```

Recommended columns:

```text
id
event_type
key
details
created_at
```

---

### 15.7 Sensitive Files That Must Not Be Committed

These files must stay out of Git:

```text
.env
data/users.csv
data/dashboard.db
data/*.db
data/*.sqlite
errors
error*
zerror
fixes
*.log
__pycache__/
*.pyc
```

Use example files instead:

```text
.env.example
data/users.example.csv
```

---

### 15.8 Security Validation Commands

Validate Python files:

```bash
cd /opt/ai-support-agent
source venv/bin/activate

PYTHONPATH=/opt/ai-support-agent python -m py_compile app/security_guard.py
PYTHONPATH=/opt/ai-support-agent python -m py_compile app/openai_realtime_bridge.py
PYTHONPATH=/opt/ai-support-agent python -m py_compile dashboard/app.py
```

Initialize security tables manually:

```bash
PYTHONPATH=/opt/ai-support-agent python -c "from app.security_guard import init_security_db; init_security_db(); print('Security DB initialized')"
```

Inspect security events:

```bash
sqlite3 /opt/ai-support-agent/data/dashboard.db "SELECT id, event_type, key, details, datetime(created_at, 'unixepoch') FROM security_events ORDER BY id DESC LIMIT 20;"
```

Inspect rate limits:

```bash
sqlite3 /opt/ai-support-agent/data/dashboard.db "SELECT key, counter, datetime(window_start, 'unixepoch'), datetime(locked_until, 'unixepoch') FROM rate_limits ORDER BY locked_until DESC LIMIT 20;"
```

---

### 15.9 Security Checklist Before Customer Demo

Before any customer-facing demo or pilot, confirm:

```text
[ ] DASHBOARD_SECRET is set and is not default.
[ ] Default dashboard passwords are changed.
[ ] Dashboard is accessible only internally or through VPN.
[ ] DASHBOARD_COOKIE_SECURE=true if HTTPS is enabled.
[ ] .env is not committed to Git.
[ ] users.csv is not committed to Git.
[ ] dashboard.db is not committed to Git.
[ ] Login rate limiting is enabled.
[ ] Verification rate limiting is enabled.
[ ] Call spam protection is enabled.
[ ] Security Events page is available for admin.
[ ] Asterisk AMI is not exposed publicly.
[ ] Port 8765 is bound to 127.0.0.1 only.
```
