# Deployment Guide — AI IT Support Voice Agent

## 1. Purpose

This guide explains how to deploy, configure, operate, and troubleshoot the AI IT Support Voice Agent project.

The system includes:

- Asterisk PBX
- Python OpenAI Realtime bridge
- Zammad ticketing integration
- SQLite dashboard database
- FastAPI dashboard
- Optional Asterisk AMI transfer-to-agent support

All commands assume the project runs from:

```text
/opt/ai-support-agent
```

---

## 2. Server Requirements

Recommended POC server:

```text
OS: Ubuntu Server 22.04 or 24.04
CPU: 8 vCPU
RAM: 16 GB
Disk: 100 GB or more
Network: Access to SIP provider, OpenAI API, Zammad, and internal/VPN users
```

Recommended production changes:

- Move SQLite to PostgreSQL
- Use HTTPS reverse proxy
- Restrict dashboard and AMI ports
- Add backup and monitoring

---

## 3. Required Ports

| Port | Purpose | Exposure |
|---|---|---|
| 8765 | Asterisk to AI bridge WebSocket | Localhost only |
| 8090 | FastAPI dashboard | VPN/internal only |
| 8080 | Zammad web UI/API | VPN/internal only |
| 5038 | Asterisk AMI | Localhost only preferred |
| 5060/5061 | SIP trunk | As required by SIP provider |

Important:

```text
Do not expose port 8765 to external networks.
Do not expose AMI 5038 publicly.
Dashboard 8090 should be VPN/internal only.
```

---

## 4. Clone or Pull Project

```bash
cd /opt

git clone <YOUR_REPO_URL> ai-support-agent
cd /opt/ai-support-agent
```

If repo already exists:

```bash
cd /opt/ai-support-agent
git pull
```

---

## 5. Python Virtual Environment

```bash
cd /opt/ai-support-agent
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
```

Install required packages:

```bash
pip install fastapi uvicorn websockets python-dotenv requests python-multipart jinja2
```

If using additional features later, update requirements accordingly.

---

## 6. Environment Configuration

Create `.env`:

```bash
cp .env.example .env
nano .env
```

Minimum required configuration:

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
```

Generate secure dashboard secret:

```bash
openssl rand -hex 32
```

Paste generated value into:

```ini
DASHBOARD_SECRET=<generated-value>
```

If dashboard uses HTTPS, set:

```ini
DASHBOARD_COOKIE_SECURE=true
```

---

## 7. User Verification CSV

Create:

```bash
nano /opt/ai-support-agent/data/users.csv
```

Example:

```csv
employee_id,name,aliases,email,phone,department,vip
1001,Ahmed Al Balushi,Ahmed Balushi|Ahmad Al Balushi|Ahmed al balooshi,ahmed@example.com,+96890000001,IT,false
1002,Mohammed Akheel,Mohammed Aqeel|Mohammad Akheel|Mohammad Aqeel|Mohammed Akeel|Mohamed Akheel,makheel@example.com,+919000000001,IT,true
1008,Ruqaiya Al Balushi,Ruqaiya Balushi|Rukayya Al Balushi|Ruqaya Al Balushi|رقية البلوشي|رقيه البلوشي,ruqaiya@example.com,+96890000008,IT,false
```

Validate verification logic:

```bash
cd /opt/ai-support-agent
source venv/bin/activate

PYTHONPATH=/opt/ai-support-agent python -c "from app.verify import verify_user; print(verify_user('1002', 'Mohammed Aqeel'))"
PYTHONPATH=/opt/ai-support-agent python -c "from app.verify import verify_user; print(verify_user('1008', 'Ahmed Al Balushi'))"
PYTHONPATH=/opt/ai-support-agent python -c "from app.verify import verify_user; print(verify_user('1008', 'Ruqaiya Al Balushi'))"
```

Expected:

```text
1002 Mohammed Aqeel     verified=True
1008 Ahmed Al Balushi   verified=False
1008 Ruqaiya Al Balushi verified=True
```

---

## 8. Validate Python Files

```bash
cd /opt/ai-support-agent
source venv/bin/activate

PYTHONPATH=/opt/ai-support-agent python -m py_compile app/config.py
PYTHONPATH=/opt/ai-support-agent python -m py_compile app/call_logger.py
PYTHONPATH=/opt/ai-support-agent python -m py_compile app/verify.py
PYTHONPATH=/opt/ai-support-agent python -m py_compile app/zammad_api.py
PYTHONPATH=/opt/ai-support-agent python -m py_compile app/openai_realtime_bridge.py
PYTHONPATH=/opt/ai-support-agent python -m py_compile dashboard/app.py
```

Expected:

```text
No output
```

---

## 9. Asterisk Configuration

Asterisk must route calls to the AI bridge using WebSocket media.

Important bridge endpoint:

```text
127.0.0.1:8765
```

Confirm Asterisk is running:

```bash
systemctl status asterisk
asterisk -rx "core show uptime"
```

Restart Asterisk:

```bash
systemctl restart asterisk
```

---

## 10. Optional Asterisk AMI for Transfer

Only required if transfer-to-agent is enabled.

Edit:

```bash
nano /etc/asterisk/manager.conf
```

Add:

```ini
[aiagent]
secret = CHANGE_ME
read = system,call,command,agent,user,originate
write = system,call,command,agent,user,originate
```

Reload AMI:

```bash
asterisk -rx "manager reload"
asterisk -rx "manager show users"
```

Update `.env`:

```ini
ASTERISK_AMI_HOST=127.0.0.1
ASTERISK_AMI_PORT=5038
ASTERISK_AMI_USER=aiagent
ASTERISK_AMI_SECRET=CHANGE_ME
ASTERISK_TRANSFER_CONTEXT=from-internal
ASTERISK_AGENT_EXTENSION=7001
ASTERISK_TRANSFER_PRIORITY=1
```

---

## 11. Multi-Queue Escalation & Audio Quality Tuning

To eliminate background noise and jitter stuttering ("broken drum" / audio underrun beeps), configure Asterisk with DSP noise reduction, adaptive jitter buffering, and tiered human queues.

### 11.1 Asterisk Audio Clean-Up & Dialplan
Edit `/etc/asterisk/extensions.conf`:

```ini
[from-internal]
; Inbound AI Bridge setup with hardware/DSP audio clean-up
exten => 7000,1,Answer()
 same => n,Set(JITTERBUFFER(adaptive)=default)
 same => n,Set(DENOISE(rx)=on)  ; Scans and cleans background noise/office murmurs
 same => n,Set(DENOISE(tx)=on)
 same => n,AudioSocket(127.0.0.1:8765) ; Or WebSocket media bridge
 same => n,Hangup()

; Queue 7001: Standard L1 IT Support Queue
exten => 7001,1,Answer()
 same => n,Set(JITTERBUFFER(adaptive)=default)
 same => n,Queue(it-support,t,,,300)
 same => n,Hangup()

; Queue 7002: Executive & VIP Concierge Queue (CEO, CFO, C-Suite)
exten => 7002,1,Answer()
 same => n,Set(JITTERBUFFER(adaptive)=default)
 same => n,Queue(it-vip-exec,t,,,60)
 same => n,Hangup()

; Queue 7003: Sev-1 Emergency & Outage Incident Queue
exten => 7003,1,Answer()
 same => n,Set(JITTERBUFFER(adaptive)=default)
 same => n,Queue(it-emergency,t,,,30)
 same => n,Hangup()
```

### 11.2 Multi-Queue Definitions
Edit `/etc/asterisk/queues.conf`:

```ini
[it-support]
musicclass=default
strategy=ringall
timeout=20
retry=5
maxlen=20
joinempty=yes
leavewhenempty=no

[it-vip-exec]
musicclass=default
strategy=ringall
timeout=15
retry=3
maxlen=5
joinempty=yes
leavewhenempty=no

[it-emergency]
musicclass=default
strategy=ringall
timeout=10
retry=2
maxlen=10
joinempty=yes
leavewhenempty=no
```

Reload Asterisk dialplan and queues:

```bash
asterisk -rx "dialplan reload"
asterisk -rx "module reload app_queue.so"
```

---

## 12. AI Bridge Systemd Service

Create service:

```bash
cat > /etc/systemd/system/ai-support-bridge.service <<'EOF'
[Unit]
Description=AI Support Agent OpenAI Realtime Bridge
After=network-online.target asterisk.service
Wants=network-online.target
Requires=asterisk.service

[Service]
Type=simple
WorkingDirectory=/opt/ai-support-agent
Environment=PYTHONPATH=/opt/ai-support-agent
ExecStart=/opt/ai-support-agent/venv/bin/python /opt/ai-support-agent/app/openai_realtime_bridge.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start:

```bash
systemctl daemon-reload
systemctl enable ai-support-bridge
systemctl start ai-support-bridge
systemctl status ai-support-bridge
```

Check port:

```bash
ss -lntp | grep 8765
```

Expected:

```text
LISTEN 127.0.0.1:8765
```

Logs:

```bash
journalctl -u ai-support-bridge -f
```

---

## 13. Dashboard Systemd Service

Create service:

```bash
cat > /etc/systemd/system/ai-dashboard.service <<'EOF'
[Unit]
Description=AI IT Support Dashboard
After=network.target

[Service]
WorkingDirectory=/opt/ai-support-agent
Environment=PYTHONPATH=/opt/ai-support-agent
ExecStart=/opt/ai-support-agent/venv/bin/uvicorn dashboard.app:app --host 0.0.0.0 --port 8090
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start:

```bash
systemctl daemon-reload
systemctl enable ai-dashboard
systemctl start ai-dashboard
systemctl status ai-dashboard
```

Check port:

```bash
ss -lntp | grep 8090
```

Dashboard URL:

```text
http://SERVER-IP:8090
```

---

## 14. First Login

Default seeded users may exist for POC:

```text
admin / admin123
user / user123
reviewer / reviewer123
```

Immediately change passwords through:

```text
/change-password
```

Before customer demo, create named users and disable default demo users.

---

## 15. Restart and Reboot Test

Restart services:

```bash
systemctl restart asterisk
systemctl restart ai-support-bridge
systemctl restart ai-dashboard
```

Check status:

```bash
systemctl status asterisk
systemctl status ai-support-bridge
systemctl status ai-dashboard
```

Reboot test:

```bash
reboot
```

After reboot:

```bash
systemctl status asterisk
systemctl status ai-support-bridge
systemctl status ai-dashboard
ss -lntp | grep 8765
ss -lntp | grep 8090
```

---

## 16. Zammad Integration Test

Check Zammad URL:

```bash
curl -I http://127.0.0.1:8080
```

Check token manually by creating a test via bridge call or direct script if available.

If tickets fail:

```bash
journalctl -u ai-support-bridge -f
```

Common issues:

- `ZAMMAD_TOKEN` missing
- Wrong group name
- Zammad API not reachable
- Customer email missing from verified user

---

## 17. Manual Bridge Test

If systemd is running, stop it first:

```bash
systemctl stop ai-support-bridge
```

Start manually:

```bash
cd /opt/ai-support-agent
source venv/bin/activate
PYTHONPATH=/opt/ai-support-agent python app/openai_realtime_bridge.py
```

If port already in use:

```bash
ss -lntp | grep 8765
pkill -9 -f openai_realtime_bridge.py
```

For production, use systemd instead of manual mode:

```bash
systemctl start ai-support-bridge
```

---

## 18. Troubleshooting

### Port 8765 already in use

```bash
ss -lntp | grep 8765
systemctl stop ai-support-bridge
pkill -9 -f openai_realtime_bridge.py
systemctl start ai-support-bridge
```

### OpenAI invalid API key

Check `.env`:

```bash
grep OPENAI_API_KEY /opt/ai-support-agent/.env
```

Do not paste keys into chat or tickets.

### TV/static noise on call

Usually caused by invalid Realtime audio/session config.

Check logs:

```bash
journalctl -u ai-support-bridge -f
```

Ensure:

```ini
VAD_IDLE_TIMEOUT_MS=30000
```

Do not set above `30000`.

### Dashboard not loading

```bash
systemctl status ai-dashboard
journalctl -u ai-dashboard -n 100 --no-pager
ss -lntp | grep 8090
curl http://127.0.0.1:8090/login | head
```

### Ongoing calls are wrong

Run stale cleanup:

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

### Zammad ticket group error

If Zammad returns group lookup error, set fallback group:

```ini
DEFAULT_ZAMMAD_GROUP=Service Desk
```

---

## 19. Backup Plan

Back up these items:

```text
/opt/ai-support-agent/.env
/opt/ai-support-agent/data/users.csv
/opt/ai-support-agent/data/dashboard.db
/var/spool/asterisk/monitor/ai-support
/etc/asterisk
/opt/zammad-docker-compose
```

Example:

```bash
tar -czf /root/ai-support-agent-backup-$(date +%F).tar.gz \
  /opt/ai-support-agent/.env \
  /opt/ai-support-agent/data \
  /etc/asterisk \
  /var/spool/asterisk/monitor/ai-support
```

---

## 20. Production Recommendations

Before production:

- Replace SQLite with PostgreSQL.
- Enable HTTPS for dashboard.
- Set `DASHBOARD_COOKIE_SECURE=true`.
- Remove default users.
- Add CSRF protection for dashboard forms.
- Use named admin accounts.
- Add structured JSON logs.
- Add monitoring for Asterisk, bridge, dashboard, Zammad, disk space.
- Add backup and restore testing.
- Add pytest suite for `verify.py`.
- Add Webex Calling CDR integration only after API access is confirmed.

---

## 21. Upgrade Procedure

Recommended code update flow:

```bash
cd /opt/ai-support-agent
git pull
source venv/bin/activate

PYTHONPATH=/opt/ai-support-agent python -m py_compile app/openai_realtime_bridge.py
PYTHONPATH=/opt/ai-support-agent python -m py_compile dashboard/app.py

systemctl restart ai-support-bridge
systemctl restart ai-dashboard

systemctl status ai-support-bridge
systemctl status ai-dashboard
```

If Zammad branding is still used:

```bash
/opt/ai-support-agent/zammad-branding/apply_zammad_branding.sh
```

---

## 22. Customer Demo Checklist

Before demo:

```bash
systemctl status asterisk
systemctl status ai-support-bridge
systemctl status ai-dashboard
ss -lntp | grep 8765
ss -lntp | grep 8090
```

Test call flow:

```text
1. Call support number.
2. Select English.
3. Verify known user.
4. Report VPN issue.
5. Confirm ticket creation.
6. Confirm ticket number is read aloud.
7. Check ticket in Zammad.
8. Check dashboard call history.
```

Arabic test:

```text
1. Select Arabic.
2. Say Arabic-supported name or alias.
3. Confirm verification is correct.
4. Confirm AI continues in Arabic only.
```

Dashboard test:

```text
1. Login as admin.
2. Check Dashboard.
3. Check Calls.
4. Check Health.
5. Check Active Calls.
6. Check Failed Calls.
7. Check Recordings.
```
