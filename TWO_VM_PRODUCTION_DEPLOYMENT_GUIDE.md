# 2-VM Production Deployment & Wiring Guide — AI IT Support Agent ("Arif")

This guide provides the complete, production-grade deployment architecture for running the National Finance AI Support Agent across your **2 dedicated Virtual Machines**.

---

## 1. Resource Allocation & Architecture Blueprint

### Compute Sizing Summary

| Virtual Machine | Compute Specs | Primary Role | Hosted Services |
|---|---|---|---|
| **VM 1: Voice & Edge** | **12 vCPU · 24 GB RAM · 300 GB SSD** | **Telephony & Realtime AI Processing** | • Asterisk PBX 20+ (SIP / RTP / SpeexDSP / RFC 4733 DTMF)<br/>• Python OpenAI Realtime Bridge (:8765)<br/>• FastAPI Telemetry & Operations Dashboard (:8090)<br/>• Asterisk AMI (:5038 localhost with capacity guard)<br/>• Active Directory (AD/LDAP) Sync Worker<br/>• Nginx Reverse Proxy (:443 HTTPS / TLS Termination) |
| **VM 2: Core & Data** | **16 vCPU · 32 GB RAM · 500 GB SSD** | **Helpdesk, Database & Archival** | • Frappe Helpdesk / ERPNext Support (:8000 / :443)<br/>• PostgreSQL 16 Enterprise Database (:5432 with connection pooling)<br/>• Redis Cache & Background Queue Workers<br/>• Call Recordings Storage (500 GB = 70,000+ hrs) |

---

## 2. End-to-End Wiring & Network Topology

```mermaid
flowchart TD
    subgraph External & Telephony
        PSTN([📞 100+ Organization Callers / SIP Trunk]) -->|SIP 5060 + RTP 10000-20000| Asterisk
        OpenAI_Cloud([☁️ OpenAI Realtime API]) <-->|WSS :443 TLS| Bridge
        Admin([👤 IT Support Admins]) -->|HTTPS :443 Nginx| Dashboard
        Agents([🎧 IT Support Human Agents]) -->|HTTPS :8000 / :443| FrappeUI
        AgentPhones([☎️ Agent Extensions 7001-7003]) <-->|SIP / RTP| Asterisk
        DomainController([🏢 Windows Server AD / DC]) <-->|LDAPS :636 / StartTLS :389| ADSync[AD Sync Worker]
    end

    subgraph VM 1: Telephony & Voice Edge [IP: 192.168.10.11]
        Asterisk[Asterisk PBX 20+<br/>SpeexDSP + JitterBuffer + RFC 4733]
        Asterisk <-->|Audio WS :8765| Bridge[Voice Bridge<br/>openai_realtime_bridge.py]
        Bridge -.->|AMI :5038 Pre-Flight Check| Asterisk
        Dashboard[FastAPI Dashboard<br/>:8090 / Nginx :443]
        ADSync -.->|Periodic Directory Sync| Bridge
    end

    subgraph VM 2: Helpdesk & Data Core [IP: 192.168.10.12]
        Frappe[Frappe Helpdesk<br/>HD Ticket / Issue API :8000]
        FrappeUI[Frappe Agent Desk Portal]
        Postgres[(PostgreSQL 16 DB<br/>:5432 Pooled)]
        Recordings[(500 GB Call Recordings<br/>/var/spool/asterisk/monitor)]
    end

    %% Inter-VM Wiring
    Bridge -->|REST API :8000 token auth| Frappe
    Bridge -->|SQL Pooled :5432| Postgres
    Dashboard -->|SQL Pooled :5432| Postgres
    Asterisk -.->|NFS mount or rsync nightly| Recordings
```

---

## 3. Network Ports, DNS & Firewall Rules

### 3.0 Internal Corporate DNS Configuration
The internal DNS entries for the environment are:
- **Operations Dashboard**: `nfitdashboard.nfc.co.om` → points to **VM 1** (`10.1.120.165`)
- **Helpdesk Ticketing System**: `nfticketing.nfc.co.om` → points to **VM 2** (`10.1.120.166`)

Network IPs:
- **VM 1 (Voice Edge & Dashboard)**: `10.1.120.165`
- **VM 2 (Core Helpdesk & Database)**: `10.1.120.166`
- **Active Directory DC**: `130.5.1.202` (Base DN: `DC=nfc,DC=co,DC=om`)


### 3.1 Firewall on VM 1 (`192.168.10.11`)

Execute on VM 1:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Administration
sudo ufw allow from 192.168.10.0/24 to any port 22 proto tcp comment 'SSH Internal'

# Telephony Inbound from Telecom / SBC Provider
sudo ufw allow 5060/udp comment 'SIP Signaling UDP'
sudo ufw allow 5060/tcp comment 'SIP Signaling TCP'
sudo ufw allow 5061/tcp comment 'SIP TLS'
sudo ufw allow 10000:20000/udp comment 'RTP Audio Stream'

# Ops Dashboard (HTTPS via Nginx Reverse Proxy)
sudo ufw allow from 192.168.10.0/24 to any port 443 proto tcp comment 'Dashboard HTTPS'
sudo ufw allow from 192.168.10.0/24 to any port 8090 proto tcp comment 'Dashboard Direct Internal'

# Active Directory LDAPS Outbound (TCP 636) or StartTLS (TCP 389)
sudo ufw allow out to 192.168.10.20 port 636 proto tcp comment 'Active Directory LDAPS'
sudo ufw allow out to 192.168.10.20 port 389 proto tcp comment 'Active Directory StartTLS'

# Strictly keep 8765 and 5038 loopback only (127.0.0.1)
sudo ufw enable
sudo ufw status verbose
```

### 3.2 Firewall on VM 2 (`192.168.10.12`)

Execute on VM 2:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Administration
sudo ufw allow from 192.168.10.0/24 to any port 22 proto tcp comment 'SSH Internal'

# Allow VM 1 to access PostgreSQL 16
sudo ufw allow from 192.168.10.11 to any port 5432 proto tcp comment 'PostgreSQL from VM1'

# Allow VM 1 (Bridge) and Human Agents to access Frappe Helpdesk
sudo ufw allow from 192.168.10.0/24 to any port 8000 proto tcp comment 'Frappe Helpdesk HTTP'
sudo ufw allow from 192.168.10.0/24 to any port 443 proto tcp comment 'Frappe Helpdesk HTTPS'

sudo ufw enable
sudo ufw status verbose
```

---

## 4. Step-by-Step Setup: VM 2 (Helpdesk & Database Core)

Deploy VM 2 first so that database endpoints and Frappe API tokens are ready for VM 1.

### 4.1 Install Base Dependencies & Docker

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw jq software-properties-common postgresql-client

# Install Docker & Docker Compose
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
newgrp docker
docker compose version
```

### 4.2 Deploy PostgreSQL 16 (Central Telemetry, KB & Caller DB)

```bash
sudo apt install -y postgresql-16 postgresql-contrib-16
```

Configure PostgreSQL to listen on the internal network:

```bash
sudo nano /etc/postgresql/16/main/postgresql.conf
```
Set:
```ini
listen_addresses = 'localhost,192.168.10.12'
max_connections = 150
shared_buffers = 2GB
effective_cache_size = 6GB
work_mem = 64MB
maintenance_work_mem = 512MB
min_wal_size = 1GB
max_wal_size = 4GB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
```

Allow VM 1 in `/etc/postgresql/16/main/pg_hba.conf`:
```ini
# TYPE  DATABASE        USER            ADDRESS                 METHOD
host    ai_dashboard    ai_user         192.168.10.11/32        scram-sha-256
```

Create user and database with **UTF-8 encoding** (mandatory for bilingual Arabic/English names and playbooks):
```bash
sudo -u postgres psql -c "CREATE USER ai_user WITH ENCRYPTED PASSWORD 'SECURE_DB_PASSWORD_HERE';"
sudo -u postgres psql -c "CREATE DATABASE ai_dashboard OWNER ai_user ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ai_dashboard TO ai_user;"
sudo systemctl restart postgresql
sudo systemctl enable postgresql
```

Test connection locally:
```bash
PGPASSWORD='SECURE_DB_PASSWORD_HERE' psql -h 127.0.0.1 -U ai_user -d ai_dashboard -c '\l'
```

### 4.3 Deploy Frappe Helpdesk via Docker Compose

```bash
mkdir -p /opt/frappe-helpdesk && cd /opt/frappe-helpdesk
git clone https://github.com/frappe/helpdesk.git .
docker compose up -d
```

Confirm all containers are healthy:
```bash
docker compose ps
```

### 4.4 Generate API Key & Secret for Arif in Frappe

1. Log into Frappe Desk (`http://192.168.10.12:8000`) as Administrator.
2. Navigate to: **Users** → **Add User**:
   - **Full Name**: `Arif Voice Agent`
   - **Email**: `arif.agent@nationalfinance.om`
   - **Role**: `Helpdesk Agent` / `System Manager`
3. Open the user profile → Click **Settings** → **API Access** → Click **Generate Keys**.
4. Save the generated credentials:
   - **API Key**: `e.g. 9812a1b32d4e5f6`
   - **API Secret**: `e.g. 7890f1e2d3c4b5a`

### 4.5 Configure Frappe SLA Policies & Teams

1. Go to **HD Team** → Create team: `IT Support`.
2. Go to **Service Level Agreement**:
   - **P0_EXECUTIVE**: Response Time: **15 mins**, Resolution: **2 hours**
   - **P1_EMERGENCY**: Response Time: **10 mins**, Resolution: **1 hour**
   - **P2_HIGH**: Response Time: **1 hour**, Resolution: **4 hours**
   - **P3_MEDIUM**: Response Time: **4 hours**, Resolution: **1 business day**
   - **P4_LOW**: Response Time: **8 hours**, Resolution: **3 business days**

---

## 5. Step-by-Step Setup: VM 1 (Voice & Telephony Edge)

### 5.1 Install Asterisk 20 with SpeexDSP Noise Reduction

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y build-essential libxml2-dev libncurses5-dev libsqlite3-dev \
  libssl-dev libjansson-dev libspeex-dev libspeexdsp-dev uuid-dev libedit-dev \
  python3-dev python3-venv python3-pip git curl ufw jq nginx

# Download and compile Asterisk 20 LTS
cd /usr/src
sudo curl -O http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-20-current.tar.gz
sudo tar -zxvf asterisk-20-current.tar.gz
cd asterisk-20.*
sudo contrib/scripts/install_prereq install
sudo ./configure --with-speex --with-speexdsp
sudo make menuselect.makeopts

# Enable Speex denoise, app_queue, and pjsip
sudo menuselect/menuselect --enable func_denoise --enable app_queue menuselect.makeopts
sudo make -j$(nproc)
sudo make install
sudo make samples
sudo make config
sudo ldconfig
```

### 5.2 Configure Asterisk Audio DSP, Queues, DTMF & Dialplan

#### A. `/etc/asterisk/extensions.conf`:
```ini
[general]
static=yes
writeprotect=no

[from-internal]
; -----------------------------------------------------------------------------
; AI Support Inbound Entry Point
; -----------------------------------------------------------------------------
exten => 7000,1,Answer()
 same => n,Set(JITTERBUFFER(adaptive)=default) ; Adaptive jitter buffer
 same => n,Set(DENOISE(rx)=on)                 ; Real-time Speex background noise cancellation
 same => n,Set(DENOISE(tx)=on)
 same => n,Set(CHANNEL(hangup_handler_push)=sub-hangup,s,1)
 same => n,AudioSocket(127.0.0.1:8765)         ; Stream bidirectional G.711 u-law audio to Python Bridge
 same => n,Hangup()

; -----------------------------------------------------------------------------
; Escalation Queues (AMI Redirect Targets with Priority Screen-Pop)
; -----------------------------------------------------------------------------
; Standard L1 IT Support Queue
exten => 7001,1,Answer()
 same => n,Set(JITTERBUFFER(adaptive)=default)
 same => n,Queue(it-support,t,,,300)
 same => n,Hangup()

; Executive & VIP Concierge Queue (CEO, CFO, C-Suite)
exten => 7002,1,Answer()
 same => n,Set(JITTERBUFFER(adaptive)=default)
 same => n,Queue(it-vip-exec,t,,,60)
 same => n,Hangup()

; Sev-1 Emergency & Outage Incident Response Queue
exten => 7003,1,Answer()
 same => n,Set(JITTERBUFFER(adaptive)=default)
 same => n,Queue(it-emergency,t,,,30)
 same => n,Hangup()

[sub-hangup]
exten => s,1,NoOp(Call ended - recording cleanup)
 same => n,Return()
```

#### B. `/etc/asterisk/queues.conf`:
```ini
[general]
persistentmembers = yes
autofill = yes
monitor-type = MixMonitor

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
retry=2
maxlen=5
joinempty=yes
leavewhenempty=no

[it-emergency]
musicclass=default
strategy=ringall
timeout=10
retry=1
maxlen=10
joinempty=yes
leavewhenempty=no
```

#### C. `/etc/asterisk/manager.conf` (AMI with Pre-flight Queue Inspection):
```ini
[general]
enabled = yes
port = 5038
bindaddr = 127.0.0.1

[aiagent]
secret = S3cur3AMIP@ssw0rd!
read = system,call,command,agent,user,originate
write = system,call,command,agent,user,originate
```

#### D. `/etc/asterisk/pjsip.conf` (DTMF Telephone Keypad Support):
Ensure all SIP endpoints and trunks specify RFC 4733 telephony DTMF:
```ini
[endpoint-template](!)
type=endpoint
context=from-internal
disallow=all
allow=ulaw
allow=alaw
dtmf_mode=rfc4733
inband_progress=yes
```

Reload Asterisk:
```bash
sudo asterisk -rx "dialplan reload"
sudo asterisk -rx "module reload app_queue.so"
sudo asterisk -rx "manager reload"
sudo asterisk -rx "pjsip reload"
```

---

### 5.3 Deploy the Modernized AI Support Agent on VM 1

```bash
cd /opt
sudo git clone <YOUR_GIT_REPO_URL> ai-support-agent
cd /opt/ai-support-agent

# Create dedicated Python 3.11+ virtual environment
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel

# Install all enterprise dependencies cleanly
pip install -r requirements.txt
```

Verify that all dependencies installed cleanly:
```bash
python3 -c "import fastapi, uvicorn, websockets, psycopg, ldap3, jinja2; print('All Core Dependencies OK')"
```

---

### 5.4 Configure `.env` on VM 1

Create `/opt/ai-support-agent/.env`:

```ini
# =============================================================================
# NATIONAL FINANCE — AI IT SUPPORT AGENT PRODUCTION CONFIGURATION
# =============================================================================

# -----------------------------------------------------------------------------
# OpenAI Realtime Voice Model Configuration
# -----------------------------------------------------------------------------
OPENAI_API_KEY="sk-proj-YOUR_ACTUAL_OPENAI_API_KEY"
OPENAI_REALTIME_MODEL="gpt-realtime"

# -----------------------------------------------------------------------------
# Ticketing System: Pointing to VM 2 Frappe Helpdesk
# -----------------------------------------------------------------------------
TICKETING_SYSTEM="frappe"
FRAPPE_URL="http://nfticketing.nfc.co.om"  # Or http://10.1.120.166:8000
FRAPPE_API_KEY="API_KEY_GENERATED_ON_VM2"
FRAPPE_API_SECRET="API_SECRET_GENERATED_ON_VM2"
FRAPPE_TICKET_DOCTYPE="HD Ticket"
FRAPPE_DEFAULT_TEAM="IT Support"

# -----------------------------------------------------------------------------
# Enterprise Database: Central PostgreSQL 16 on VM 2 with Connection Pooling
# -----------------------------------------------------------------------------
DATABASE_URL="postgresql://ai_user:SECURE_DB_PASSWORD_HERE@192.168.10.12:5432/ai_dashboard"
DB_POOL_MIN=2
DB_POOL_MAX=20

# -----------------------------------------------------------------------------
# Telephony & Multi-Queue Configuration (Asterisk on VM 1)
# -----------------------------------------------------------------------------
ASTERISK_AMI_HOST="127.0.0.1"
ASTERISK_AMI_PORT=5038
ASTERISK_AMI_USER="aiagent"
ASTERISK_AMI_SECRET="S3cur3AMIP@ssw0rd!"
ASTERISK_TRANSFER_CONTEXT="from-internal"

# Escalation Extensions
ASTERISK_QUEUE_STANDARD="7001"
ASTERISK_QUEUE_EXECUTIVE="7002"
ASTERISK_QUEUE_EMERGENCY="7003"

# Pre-flight Capacity Inspection Queues
ENFORCE_QUEUE_CAPACITY="true"
ASTERISK_QUEUE_NAME_STANDARD="it-support"
ASTERISK_QUEUE_NAME_EXECUTIVE="it-vip-exec"
ASTERISK_QUEUE_NAME_EMERGENCY="it-emergency"

# -----------------------------------------------------------------------------
# Active Directory (AD / LDAP) Enterprise Sync Connector
# -----------------------------------------------------------------------------
AD_ENABLED="true"
AD_SERVER="ldaps://192.168.10.20"
AD_PORT=636
AD_USE_SSL="true"
AD_USE_STARTTLS="false"
AD_VERIFY_CERT="false"
AD_BIND_DN="CN=svc-ai-agent,OU=ServiceAccounts,DC=nationalfinance,DC=local"
AD_PASSWORD="SecureServiceAccountPassword123!"
AD_BASE_DN="DC=nationalfinance,DC=local"
AD_SEARCH_FILTER="(&(objectCategory=person)(objectClass=user))"
AD_PAGE_SIZE=500
AD_SYNC_INTERVAL_MINUTES=30
AD_P0_GROUPS="C-Suite,Executives,CEO,CFO"
AD_P1_GROUPS="Directors,Heads,VIP"

# -----------------------------------------------------------------------------
# Voice Activity Detection (VAD) Tuned for Branch / Office Environment (1s silence wait)
# -----------------------------------------------------------------------------
VAD_THRESHOLD=0.60
VAD_SILENCE_MS=1000
VAD_IDLE_TIMEOUT_MS=30000

# -----------------------------------------------------------------------------
# Concurrency & Sizing
# -----------------------------------------------------------------------------
MAX_CONCURRENT_CALLS=25
CALL_MAX_SECONDS=1800

# -----------------------------------------------------------------------------
# Dashboard Operations & Security
# -----------------------------------------------------------------------------
DASHBOARD_SECRET="A_STRONG_RANDOM_32_CHAR_SECRET_KEY_HERE"
DASHBOARD_COOKIE_SECURE="true"
DASHBOARD_HOST="127.0.0.1"
DASHBOARD_PORT=8090
INITIAL_ADMIN_PASSWORD="YourSecureAdminPassword123!"
```

---

### 5.5 Initialize Enterprise Database & Auto-Seed Playbooks

Execute this command on VM 1 **once** prior to launching services:

```bash
cd /opt/ai-support-agent
source venv/bin/activate
python3 -c "import app.db as db; db.init_db(); print('Database schema & knowledge base playbooks successfully initialized in PostgreSQL 16!')"
```

This single command automatically:
1. Validates connection to PostgreSQL on VM 2.
2. Creates all tables (`calls`, `callers`, `knowledge_articles`, `users`, `sessions`, `settings`, `audit_logs`, `prompt_versions`).
3. Creates composite B-Tree indexes for high-throughput concurrency.
4. Auto-seeds all 14 bilingual IT troubleshooting playbooks from `knowledge_base/*.md` into `knowledge_articles`.
5. Auto-seeds initial callers from `data/users.csv` into `callers`.

---

### 5.6 Configure Nginx Reverse Proxy with HTTPS on VM 1

Create `/etc/nginx/sites-available/ai-support.conf`:

```nginx
server {
    listen 80;
    server_name ops.nationalfinance.om;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ops.nationalfinance.om;

    ssl_certificate /etc/ssl/certs/nationalfinance_wildcard.crt;
    ssl_certificate_key /etc/ssl/private/nationalfinance_wildcard.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Security Headers
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # Static Assets Cache
    location /static/ {
        alias /opt/ai-support-agent/dashboard/static/;
        expires 7d;
        add_header Cache-Control "public, no-transform";
    }

    location /brand-assets/ {
        alias /opt/ai-support-agent/brand-assets/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    # Proxy to FastAPI Dashboard
    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

Enable the Nginx configuration:
```bash
sudo ln -sf /etc/nginx/sites-available/ai-support.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

### 5.7 Create Systemd Services on VM 1

#### Service 1: AI Realtime Bridge (`/etc/systemd/system/ai-support-bridge.service`)

```ini
[Unit]
Description=AI Support Agent Realtime Voice Bridge
After=network-online.target asterisk.service
Wants=network-online.target
Requires=asterisk.service

[Service]
Type=simple
WorkingDirectory=/opt/ai-support-agent
Environment=PYTHONPATH=/opt/ai-support-agent
ExecStart=/opt/ai-support-agent/venv/bin/python /opt/ai-support-agent/app/openai_realtime_bridge.py
Restart=always
RestartSec=3
User=root

# Sizing & File Descriptor Limits for Multi-Call Load
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

#### Service 2: Dashboard Ops UI (`/etc/systemd/system/ai-dashboard.service`)

```ini
[Unit]
Description=AI IT Support Telemetry & Management Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/ai-support-agent
Environment=PYTHONPATH=/opt/ai-support-agent
ExecStart=/opt/ai-support-agent/venv/bin/uvicorn dashboard.app:app --host 127.0.0.1 --port 8090 --workers 4
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

Enable and start services:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ai-support-bridge ai-dashboard
sudo systemctl restart ai-support-bridge ai-dashboard
```

Verify service status:

```bash
sudo systemctl status ai-support-bridge --no-pager
sudo systemctl status ai-dashboard --no-pager
sudo ss -lntp | grep -E "8765|8090|5038"
```

---

## 6. End-to-End Validation & Verification Checklist

### Test 1: Cross-VM Network & PostgreSQL Check
From VM 1, test connectivity to VM 2 services:

```bash
# Verify Frappe Helpdesk port
nc -zv 192.168.10.12 8000

# Verify PostgreSQL 16 port
nc -zv 192.168.10.12 5432

# Verify PostgreSQL connection with credentials
psql "postgresql://ai_user:SECURE_DB_PASSWORD_HERE@192.168.10.12:5432/ai_dashboard" -c "SELECT COUNT(*) FROM knowledge_articles;"
```

### Test 2: Active Directory (AD) Handshake Test
Test domain controller binding and round-trip latency via dashboard API:

```bash
curl -s -X POST http://127.0.0.1:8090/api/ad/test-connection | jq .
```
Expected output:
```json
{
  "success": true,
  "roundtrip_ms": 8.4,
  "server": "ldaps://192.168.10.20:636",
  "base_dn": "DC=nationalfinance,DC=local",
  "message": "LDAPS handshake & bind successful"
}
```

### Test 3: Zero-Downtime Knowledge Base Reflection
1. Log into Dashboard at `https://ops.nationalfinance.om/knowledge`.
2. Click **Create New Playbook**:
   - ID: `oracle_erp`
   - Title: `Oracle Financials Account Unlock`
   - Category: `Enterprise Systems`
   - English Keywords: `oracle, erp, login error`
   - Arabic Keywords: `اوراكل, نظام اوراكل`
   - Content: `# Oracle ERP\n1. Clear browser cache.\n2. Reset SSO credentials.`
3. Save the playbook.
4. Without restarting any service, place a test call and ask in Arabic: *"عندي مشكلة في نظام اوراكل"*.
5. Arif immediately retrieves the new playbook steps!

### Test 4: Pre-Flight Queue Availability Guard
1. Log human agents out of Asterisk queue `7001` (`asterisk -rx "queue remove member ..."`).
2. Call Arif and report an unresolved issue.
3. Arif checks queue availability via AMI: detects 0 available agents.
4. Instead of dropping the call into an endless ring, Arif politely apologizes and automatically offers a scheduled callback (`request_callback`).

### Test 5: In-Band DTMF Keypad Fallback Test
1. Call from a noisy environment.
2. When Arif asks for Employee ID, dial `1002#` on your phone dialpad.
3. Arif captures the RFC 4733 DTMF tones, verifies employee Mansoor Al-Habsi, and greets him by name.

### Test 6: VIP Concierge & Emergency Routing
- **CEO / CFO**: Calls from registered number bypass AI troubleshooting directly to queue `7002`.
- **Emergency Sev-1**: Uttering *"System down and core banking offline"* creates an immediate Critical P1 ticket in Frappe Helpdesk and transfers to incident queue `7003`.

---

## 7. Disaster Recovery & Backup Plan

1. **Automated PostgreSQL Dumps (VM 2)**:
   ```bash
   sudo crontab -e
   # Daily backup at 02:00 AM compressed
   0 2 * * * pg_dump -U ai_user -h 127.0.0.1 ai_dashboard | gzip > /backup/db/dashboard_$(date +\%F).sql.gz
   ```
2. **Audio Retention Pruning**:
   The dashboard periodically prunes recordings older than `recording_retention_days` (default 30 days) automatically via `enforce_recording_retention()`.
3. **Repository Backup**:
   All knowledge articles edited in the dashboard automatically write to disk at `/opt/ai-support-agent/knowledge_base/*.md`, making daily Git backups trivial:
   ```bash
   cd /opt/ai-support-agent && git add knowledge_base/ data/users.csv && git commit -m "Auto-backup $(date +%F)" && git push origin main
   ```
