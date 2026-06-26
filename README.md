# AI IT Support Voice Agent — Arif

## 1. Project Overview

**Arif** is a bilingual AI voice agent for National Finance Oman IT Support. The system receives phone calls through Asterisk, streams live audio to OpenAI Realtime, verifies the caller, performs controlled IT support workflow, creates support tickets, records calls, and exposes operational data in a FastAPI dashboard.

The current POC supports:

- English and Arabic voice interaction
- Asterisk SIP calling using G.711 u-law audio
- OpenAI Realtime voice agent bridge
- Caller verification using `users.csv`
- Zammad ticket creation
- Call recording through Asterisk
- SQLite call logging
- FastAPI dashboard on port `8090`
- Admin, user, and quality reviewer roles
- Health, active calls, failed calls, recordings, settings, users, and prompt history pages

---

## 2. High-Level Architecture

```text
Caller
  ↓
SIP Provider / DID
  ↓
Asterisk PBX
  ↓
WebSocket Media Bridge on 127.0.0.1:8765
  ↓
Python OpenAI Realtime Bridge
  ├── OpenAI Realtime API
  ├── verify.py using users.csv
  ├── Zammad API ticket creation
  ├── call_logger.py SQLite logging
  └── optional transfer_to_agent via Asterisk AMI
  ↓
Dashboard FastAPI on 0.0.0.0:8090
```

---

## 3. Main Components

### 3.1 Asterisk

Asterisk handles:

- SIP trunk registration
- Incoming call routing
- Media WebSocket connection to Python bridge
- Call recording using WAV files
- Optional queue or transfer to live agents

Important ports:

```text
5060/5061  SIP, depending on trunk setup
8765       Local WebSocket bridge, bound to 127.0.0.1
5038       AMI, only if transfer-to-agent is enabled
```

---

### 3.2 OpenAI Realtime Bridge

Main file:

```text
app/openai_realtime_bridge.py
```

Responsibilities:

- Accept Asterisk WebSocket media
- Connect to OpenAI Realtime API
- Send and receive u-law audio
- Manage call lifecycle
- Apply strict call flow state machine
- Handle language selection
- Verify users
- Create tickets
- Queue responses safely
- Prevent overlapping active responses
- Log calls to dashboard database

Important local port:

```text
127.0.0.1:8765
```

---

### 3.3 Verification

Main file:

```text
app/verify.py
```

Verification uses:

```text
data/users.csv
```

Expected CSV format:

```csv
employee_id,name,aliases,email,phone,department,vip
1002,Mohammed Akheel,Mohammed Aqeel|Mohammad Akheel,makheel@example.com,+919000000001,IT,true
```

Verification rules:

- Employee ID must match exactly.
- Name must match official name or alias.
- First name/given name must match.
- Common family tokens like `Al`, `bin`, `bint`, and Arabic equivalents are ignored for stronger matching.
- VIP users can be flagged using `vip=true`.

---

### 3.4 Ticketing

Current ticketing backend:

```text
Zammad
```

Main file:

```text
app/zammad_api.py
```

The bridge creates tickets with:

- Caller name
- Employee ID
- Email
- Department
- Issue summary
- Key points collected
- Troubleshooting steps
- Created by AI Voice Agent Arif

Future recommended backend:

```text
Frappe Helpdesk
```

Reason:

- Modern UI
- Better customer-facing experience
- Good fit for a clean helpdesk portal
- Better long-term replacement for Zammad if customer rejects Zammad UI

---

### 3.5 Dashboard

Main file:

```text
dashboard/app.py
```

Dashboard service:

```text
ai-dashboard.service
```

Dashboard URL:

```text
http://SERVER-IP:8090
```

Dashboard roles:

| Role | Access |
|---|---|
| admin | Dashboard, calls, active calls, failed calls, recordings, health, prompts, users, settings |
| user | Dashboard, calls, active calls, failed calls, change password |
| quality_reviewer | Recordings, quality review, change password |

Security notes:

- Users should use strong passwords.
- Default credentials must be changed before customer demo or pilot.
- Dashboard should only be exposed over VPN or internal secure network.
- Do not expose dashboard directly to the public internet.

---

## 4. Directory Structure

```text
/opt/ai-support-agent
├── app/
│   ├── openai_realtime_bridge.py
│   ├── config.py
│   ├── verify.py
│   ├── zammad_api.py
│   ├── call_logger.py
│   └── transfer.py
│
├── dashboard/
│   └── app.py
│
├── data/
│   ├── users.csv
│   ├── users.example.csv
│   └── dashboard.db
│
├── zammad-branding/
│   ├── apply_zammad_branding.sh
│   ├── national_finance.css
│   └── assets/
│       ├── nfc-logo.svg
│       └── tct-logo.png
│
├── .env
├── .env.example
├── .gitignore
├── README.md
└── DEPLOYMENT.md
```

---

## 5. Environment Variables

Main config file:

```text
/opt/ai-support-agent/.env
```

Example:

```ini
OPENAI_API_KEY=sk-proj-CHANGE_ME
OPENAI_REALTIME_MODEL=gpt-realtime

ZAMMAD_URL=http://127.0.0.1:8080
ZAMMAD_TOKEN=CHANGE_ME
DEFAULT_ZAMMAD_GROUP=Service Desk
ZAMMAD_TIMEOUT=8

CSV_USERS_FILE=/opt/ai-support-agent/data/users.csv

MAX_CONCURRENT_CALLS=10
CALL_MAX_SECONDS=1800

VAD_THRESHOLD=0.75
VAD_SILENCE_MS=1700
VAD_IDLE_TIMEOUT_MS=30000

DASHBOARD_SECRET=CHANGE_ME_LONG_RANDOM_SECRET
DASHBOARD_COOKIE_SECURE=false

SIMPLE_ISSUE_AUTO_TICKET=true

ASTERISK_AMI_HOST=127.0.0.1
ASTERISK_AMI_PORT=5038
ASTERISK_AMI_USER=aiagent
ASTERISK_AMI_SECRET=CHANGE_ME
ASTERISK_TRANSFER_CONTEXT=from-internal
ASTERISK_AGENT_EXTENSION=7001
ASTERISK_TRANSFER_PRIORITY=1
```

Generate dashboard secret:

```bash
openssl rand -hex 32
```

---

## 6. Services

### 6.1 AI Bridge Service

Service file:

```text
/etc/systemd/system/ai-support-bridge.service
```

Useful commands:

```bash
systemctl status ai-support-bridge
systemctl restart ai-support-bridge
journalctl -u ai-support-bridge -f
```

Check bridge port:

```bash
ss -lntp | grep 8765
```

---

### 6.2 Dashboard Service

Service file:

```text
/etc/systemd/system/ai-dashboard.service
```

Useful commands:

```bash
systemctl status ai-dashboard
systemctl restart ai-dashboard
journalctl -u ai-dashboard -f
```

Check dashboard port:

```bash
ss -lntp | grep 8090
```

---

### 6.3 Asterisk Service

Useful commands:

```bash
systemctl status asterisk
systemctl restart asterisk
asterisk -rx "core show uptime"
```

---

## 7. Call Flow

```text
1. Caller dials IT support number.
2. Asterisk receives the call and starts recording.
3. Asterisk opens WebSocket media to 127.0.0.1:8765.
4. Python bridge connects to OpenAI Realtime.
5. AI says greeting and asks Arabic or English.
6. Caller selects language.
7. AI asks name and employee ID.
8. Backend verifies user from users.csv.
9. AI asks issue details.
10. AI performs troubleshooting or simple issue ticket flow.
11. If required, AI creates ticket in Zammad.
12. AI reads ticket number.
13. AI asks if anything else is needed.
14. AI says goodbye.
15. Call is closed and logged in dashboard DB.
```

---

## 8. Dashboard Features

Current dashboard includes:

- Login page with National Finance branding
- TCT Enterprise footer credit
- Dashboard summary
- Call history with filters
- Active calls
- Failed/rejected calls
- Health checks
- Recordings review
- Admin settings
- User management
- Prompt version history
- Change password

---

## 9. Health Checks

Dashboard health page:

```text
/health
```

Checks:

- AI bridge port `8765`
- AI bridge service
- Asterisk service
- Dashboard service
- Zammad API reachability
- OpenAI key configured status without exposing the key

CLI checks:

```bash
systemctl status ai-support-bridge
systemctl status ai-dashboard
systemctl status asterisk
ss -lntp | grep 8765
ss -lntp | grep 8090
curl http://127.0.0.1:8090/login
```

---

## 10. Security Notes

Before any customer-facing pilot:

- Set a strong `DASHBOARD_SECRET`.
- Change all default dashboard passwords.
- Do not commit `.env`, `users.csv`, or `dashboard.db` to Git.
- Restrict dashboard port `8090` to VPN/internal users only.
- Restrict Asterisk AMI port `5038` to localhost or trusted hosts only.
- Use HTTPS or VPN access for dashboard.
- Set `DASHBOARD_COOKIE_SECURE=true` when HTTPS is enabled.

---

## 11. Git Hygiene

Sensitive files that must not be tracked:

```text
.env
data/users.csv
data/dashboard.db
errors
error*
zerror
fixes
*.log
```

Use example files instead:

```text
.env.example
data/users.example.csv
```

---

## 12. Known Limitations

- Webex integration is not implemented yet.
- Zammad is the current ticketing backend, but customer may prefer a modern replacement.
- Frappe Helpdesk is recommended as the next ticketing POC.
- Live speaker diarization is not implemented; background voices can still affect AI accuracy.
- Noise suppression is limited by the PSTN/SIP audio path.
- Existing dashboard uses SQLite; PostgreSQL is recommended before production.

---

## 13. Recommended Roadmap

### Phase 1 — Security and reliability

- Secure sessions
- Required dashboard secret
- Duplicate call row fix
- Stale call reconciliation
- Config validation
- Non-blocking verify and ticket operations

### Phase 2 — Customer call behavior

- VIP users
- Simple issue auto-ticket
- Transfer to human queue
- Better Asterisk queue status

### Phase 3 — Ticketing replacement

- Evaluate Frappe Helpdesk
- Build ticketing abstraction
- Implement Frappe API client
- Migrate away from Zammad if approved

### Phase 4 — Webex Calling stats

- Confirm Webex Calling API access
- Pull Detailed Call History records
- Show Webex stats in dashboard

### Phase 5 — Production hardening

- PostgreSQL migration
- HTTPS
- Backup and restore plan
- Monitoring and alerting
- Structured JSON logs
- Automated test suite

---

## 14. Useful Commands

Restart everything:

```bash
systemctl restart asterisk
systemctl restart ai-support-bridge
systemctl restart ai-dashboard
```

Watch bridge logs:

```bash
journalctl -u ai-support-bridge -f
```

Watch dashboard logs:

```bash
journalctl -u ai-dashboard -f
```

Check latest calls:

```bash
sqlite3 /opt/ai-support-agent/data/dashboard.db "select id, call_id, caller_number, employee_id, verified_name, status, ticket_number from calls order by id desc limit 10;"
```

Clean stale calls manually:

```bash
sqlite3 /opt/ai-support-agent/data/dashboard.db "
UPDATE calls
SET status='ended',
    end_time=COALESCE(end_time, strftime('%s','now')),
    duration_seconds=CASE
        WHEN start_time IS NOT NULL THEN strftime('%s','now') - start_time
        ELSE duration_seconds
    END
WHERE end_time IS NULL
AND start_time < strftime('%s','now') - 1800
AND status IN ('in_progress','language_selected','verified','troubleshooting');
"
```
