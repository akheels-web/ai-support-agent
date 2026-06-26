# Production Deployment Guide — AI IT Support Voice Agent “Arif”

## 1. Document Purpose

This document describes the recommended production architecture for deploying the AI IT Support Voice Agent platform for a finance customer.

The production design covers:

- VM sizing
- Number of VMs
- Network connectivity
- Security zones
- High availability options
- Database design
- Asterisk and SIP architecture
- AI bridge architecture
- Dashboard architecture
- Ticketing system architecture
- Backup and disaster recovery
- Monitoring and alerting
- Production cutover checklist

This document is written for IT, infrastructure, DevOps, telecom, and security teams who need to deploy and operate the platform in production.

---

## 2. Current POC Components

The current POC contains these main components:

```text
Caller / SIP trunk
    ↓
Asterisk PBX
    ↓
Python OpenAI Realtime Bridge on port 8765
    ↓
OpenAI Realtime API
    ↓
Verification from users.csv
    ↓
Ticketing system
    ↓
SQLite dashboard database
    ↓
FastAPI dashboard on port 8090
```

Current POC services:

```text
ai-support-bridge.service
ai-dashboard.service
asterisk.service
zammad / future helpdesk system
```

Current POC storage:

```text
/opt/ai-support-agent/data/dashboard.db
/opt/ai-support-agent/data/users.csv
/var/spool/asterisk/monitor/ai-support
```

---

## 3. Production Architecture Principles

For production, the system should follow these principles:

- Separate public-facing voice/SIP components from application and database components.
- Avoid SQLite for production. Move operational state to PostgreSQL.
- Keep OpenAI API keys and ticketing tokens only in server-side environment variables or secret vaults.
- Keep port `8765` private and reachable only from Asterisk or internal media bridge hosts.
- Keep dashboard access internal/VPN-only.
- Use HTTPS for dashboard and ticketing portals.
- Use backups for database, configuration, recordings, and ticket attachments.
- Add monitoring for call quality, failed calls, ticket failures, CPU, memory, disk, and service health.
- Design for scale by increasing AI bridge workers horizontally.

---

## 4. Recommended Production VM Layout

### 4.1 Recommended Standard Production Layout

For the first production deployment, use **6 VMs**.

```text
VM-01  Asterisk PBX / SIP Media Gateway
VM-02  AI Bridge Application
VM-03  Dashboard / Admin Portal
VM-04  PostgreSQL Database
VM-05  Helpdesk / Ticketing System
VM-06  Monitoring + Backup Utility
```

This layout gives clean separation between telephony, AI processing, dashboard/admin, database, ticketing, and monitoring.

---

## 5. VM Specifications

### 5.1 VM-01 — Asterisk PBX / SIP Media Gateway

Purpose:

- Receive SIP calls
- Handle SIP trunk connectivity
- Start Asterisk recording
- Route audio to AI bridge WebSocket
- Optional transfer to human agent queue

Recommended size for initial production:

```text
CPU: 4 vCPU
RAM: 8 GB
Disk: 100 GB SSD
OS: Ubuntu Server 24.04 LTS
Network: Low latency to SIP provider and AI bridge
```

Recommended size for high call volume:

```text
CPU: 8 vCPU
RAM: 16 GB
Disk: 200 GB SSD
```

Notes:

- Asterisk sizing depends heavily on concurrent calls, codecs, call recording, conferencing, transcoding, and DSP work.
- If all calls use G.711 u-law and avoid transcoding, CPU usage is lower.
- If transcoding, conferencing, or echo cancellation is added, CPU requirements increase.

Key packages:

```text
asterisk
asterisk-modules
pjsip modules
logrotate
fail2ban
```

Critical ports:

```text
5060/5061 SIP
10000-20000 RTP, based on Asterisk rtp.conf
5038 AMI, localhost or trusted management network only
8765 destination to AI bridge, internal only
```

---

### 5.2 VM-02 — AI Bridge Application

Purpose:

- Run `openai_realtime_bridge.py`
- Accept Asterisk WebSocket media
- Connect to OpenAI Realtime API
- Perform verification
- Execute ticket creation
- Perform rate limiting and abuse protection
- Save call metadata to database

Recommended size for 10–20 concurrent calls:

```text
CPU: 8 vCPU
RAM: 16 GB
Disk: 100 GB SSD
OS: Ubuntu Server 24.04 LTS
```

Recommended size for 30–50 concurrent calls:

```text
CPU: 16 vCPU
RAM: 32 GB
Disk: 150 GB SSD
```

Service:

```text
ai-support-bridge.service
```

Key files:

```text
/opt/ai-support-agent/app/openai_realtime_bridge.py
/opt/ai-support-agent/app/config.py
/opt/ai-support-agent/app/verify.py
/opt/ai-support-agent/app/call_logger.py
/opt/ai-support-agent/app/security_guard.py
/opt/ai-support-agent/.env
```

Important settings:

```ini
MAX_CONCURRENT_CALLS=10
CALL_MAX_SECONDS=1800
VAD_THRESHOLD=0.75
VAD_SILENCE_MS=1700
VAD_IDLE_TIMEOUT_MS=30000
```

Critical ports:

```text
8765 inbound from Asterisk only
443 outbound to OpenAI API
443/80 outbound/internal to ticketing system
5432 outbound/internal to PostgreSQL, after PostgreSQL migration
```

Production recommendation:

- Run at least two AI bridge VMs for HA once call volume grows.
- Use Asterisk dialplan routing or a TCP load balancer to distribute calls across bridge nodes.
- Keep `8765` private.

---

### 5.3 VM-03 — Dashboard / Admin Portal

Purpose:

- Run FastAPI dashboard
- Show call history, active calls, failed calls, recordings, health, users, settings, prompts, and security events
- Provide admin control plane

Recommended size:

```text
CPU: 4 vCPU
RAM: 8 GB
Disk: 80 GB SSD
OS: Ubuntu Server 24.04 LTS
```

Service:

```text
ai-dashboard.service
```

Application port:

```text
8090
```

Production access:

```text
HTTPS through reverse proxy
VPN or internal network only
No direct public exposure
```

Recommended reverse proxy:

```text
Nginx or HAProxy
TLS certificate from internal CA or public CA
```

Important security settings:

```ini
DASHBOARD_SECRET=<long-random-secret>
DASHBOARD_COOKIE_SECURE=true
```

---

### 5.4 VM-04 — PostgreSQL Database

Purpose:

- Replace SQLite for production
- Store dashboards, calls, sessions, audit logs, security events, memory records, and Webex stats later

Recommended size for initial production:

```text
CPU: 4 vCPU
RAM: 16 GB
Disk: 200 GB NVMe SSD
OS: Ubuntu Server 24.04 LTS
Database: PostgreSQL 16 or newer
```

Recommended size for larger production:

```text
CPU: 8 vCPU
RAM: 32 GB
Disk: 500 GB NVMe SSD
```

Recommended volumes:

```text
/data/postgresql      PostgreSQL data
/backup/postgresql    Local backup staging
```

Production settings:

```text
WAL enabled
Daily full backups
Point-in-time recovery if possible
Database user separation
TLS between app and database if network policy requires
```

Why PostgreSQL:

- Better concurrency than SQLite
- Better backup and restore tooling
- Better auditability
- Suitable for high-volume dashboard and reporting
- Required before long-term production use

---

### 5.5 VM-05 — Helpdesk / Ticketing System

Purpose:

- Host the customer-approved ticketing system
- Receive tickets from the AI bridge
- Provide agent UI and customer support workflows

Current POC:

```text
Zammad
```

Recommended production replacement:

```text
Frappe Helpdesk
```

Recommended size for Frappe Helpdesk initial production:

```text
CPU: 8 vCPU
RAM: 16 GB
Disk: 200 GB SSD
OS: Ubuntu Server 24.04 LTS
Deployment: Docker Compose
```

Recommended size for larger production:

```text
CPU: 16 vCPU
RAM: 32 GB
Disk: 500 GB SSD
```

Frappe services usually include:

```text
frontend / nginx
backend / gunicorn
websocket
queue workers
scheduler
MariaDB or PostgreSQL, depending chosen deployment
Redis cache
Redis queue
persistent sites volume
```

Production recommendation:

- Start with Frappe Helpdesk POC before replacing Zammad.
- Use Docker Compose for easier upgrades.
- Keep ticketing database and files backed up.
- Use HTTPS.
- Integrate AI bridge using API token or service account.

Alternative if customer wants ITSM + asset management:

```text
GLPI
```

GLPI is better when the customer needs:

- Asset inventory
- CMDB
- ITIL processes
- License tracking
- Hardware lifecycle management

---

### 5.6 VM-06 — Monitoring + Backup Utility

Purpose:

- Monitor production services
- Store backup scripts
- Run backup jobs
- Collect logs and metrics

Recommended size:

```text
CPU: 4 vCPU
RAM: 8 GB
Disk: 300 GB SSD or attached backup volume
OS: Ubuntu Server 24.04 LTS
```

Recommended tools:

```text
Prometheus
Grafana
Loki or Graylog
Node Exporter
Blackbox Exporter
Asterisk exporter if available
Restic or BorgBackup
rclone for S3-compatible backup storage
```

Recommended monitoring targets:

```text
Asterisk service status
AI bridge service status
Dashboard service status
PostgreSQL health
Ticketing system health
Disk space
CPU and memory
Failed calls
Rejected calls
Ticket creation failures
OpenAI API failures
Security events
Recording directory growth
```

---

## 6. Production Network Architecture

### 6.1 Network Zones

Recommended zones:

```text
Internet / SIP Provider Zone
    ↓
Voice DMZ
    ↓
Application Network
    ↓
Database Network
    ↓
Backup / Monitoring Network
```

### 6.2 Connectivity Diagram

```text
SIP Provider / PSTN
        │
        │ SIP/RTP
        ▼
VM-01 Asterisk PBX
        │
        │ WebSocket audio, internal only, port 8765
        ▼
VM-02 AI Bridge
        │
        ├── HTTPS outbound to OpenAI API
        │
        ├── HTTPS/API to Helpdesk VM
        │
        └── PostgreSQL to DB VM

Admin / IT Team
        │
        │ HTTPS over VPN/internal network
        ▼
VM-03 Dashboard
        │
        └── PostgreSQL to DB VM

VM-06 Monitoring
        ├── Service checks
        ├── Metrics scraping
        └── Backup jobs
```

---

## 7. Firewall Rules

### 7.1 Asterisk VM

Allow inbound:

```text
SIP provider IPs → 5060/5061
SIP provider IPs → RTP range 10000-20000
Admin VPN → SSH 22
Monitoring VM → node exporter port
```

Allow outbound:

```text
Asterisk VM → AI Bridge VM:8765
Asterisk VM → Monitoring VM
```

Block:

```text
Public access to 8765
Public access to 5038
Public access to dashboard/database
```

---

### 7.2 AI Bridge VM

Allow inbound:

```text
Asterisk VM → 8765
Admin VPN → SSH 22
Monitoring VM → node exporter port
```

Allow outbound:

```text
AI Bridge VM → OpenAI API:443
AI Bridge VM → Helpdesk API:443/80 internal
AI Bridge VM → PostgreSQL VM:5432
AI Bridge VM → Asterisk AMI:5038, only if transfer feature is enabled
```

---

### 7.3 Dashboard VM

Allow inbound:

```text
Admin VPN / internal users → HTTPS 443
Admin VPN → SSH 22
Monitoring VM → node exporter port
```

Allow outbound:

```text
Dashboard VM → PostgreSQL VM:5432
Dashboard VM → Asterisk VM for health checks, optional
Dashboard VM → AI Bridge VM for health checks, optional
```

---

### 7.4 Database VM

Allow inbound:

```text
AI Bridge VM → 5432
Dashboard VM → 5432
Ticketing VM → database port, if same DB cluster is used
Monitoring VM → monitoring exporter port
```

Block:

```text
Public internet → database port
```

---

## 8. Production Storage Design

### 8.1 Recordings Storage

Current path:

```text
/var/spool/asterisk/monitor/ai-support
```

Production recommendation:

```text
Dedicated disk or mounted volume
Minimum 500 GB for initial production
Retention policy: 30, 60, or 90 days depending customer policy
```

Recommended layout:

```text
/recordings/ai-support/YYYY/MM/DD/
```

Recommended retention:

```text
30 days for POC
60–90 days for production, subject to legal/compliance approval
```

Important:

- Confirm customer policy before recording calls.
- If users must not see recording references, hide recordings from normal user role.
- Restrict recording playback to admin and quality reviewer roles only.

---

### 8.2 Database Storage

PostgreSQL data path:

```text
/var/lib/postgresql
```

Recommended:

```text
NVMe SSD
Daily backups
WAL archiving if possible
Encryption at rest if available
```

---

### 8.3 Ticketing Attachments

Ticketing system files should be stored on persistent storage.

For Frappe/Helpdesk Docker deployments, persist:

```text
sites volume
logs volume
backup volume
MariaDB/PostgreSQL volume
Redis data if required
```

---

## 9. Production Security Requirements

### 9.1 Secrets

Do not store secrets in Git.

Never commit:

```text
.env
OpenAI API key
Ticketing API token
Asterisk AMI password
Dashboard secret
Database password
users.csv
```

Use one of these:

```text
.env with strict Linux permissions for initial production
HashiCorp Vault for mature production
Azure Key Vault / AWS Secrets Manager if cloud-managed
```

Recommended Linux permissions:

```bash
chown root:root /opt/ai-support-agent/.env
chmod 600 /opt/ai-support-agent/.env
```

---

### 9.2 Dashboard Security

Production dashboard must have:

```text
HTTPS only
VPN/internal access only
CSRF protection
Login rate limiting
Server-side sessions
Secure cookies
Strong passwords
Admin user audit logs
Security Events page
```

Required `.env`:

```ini
DASHBOARD_SECRET=<long-random-secret>
DASHBOARD_COOKIE_SECURE=true
```

---

### 9.3 Voice Abuse Protection

Required `.env`:

```ini
CALLS_PER_NUMBER_LIMIT=5
CALLS_PER_NUMBER_WINDOW=600
CALLS_PER_NUMBER_LOCK=900

VERIFY_FAIL_LIMIT=5
VERIFY_FAIL_WINDOW=3600
VERIFY_FAIL_LOCK=3600
```

Purpose:

- Limit repeated calls from same caller number
- Prevent employee ID/name enumeration
- Log verification failures
- Block repeated abuse

---

### 9.4 Asterisk Security

Production Asterisk must include:

```text
SIP provider IP allowlisting
Strong SIP credentials
No anonymous SIP calls
Fail2ban for SIP scanning
AMI bound to localhost or private network
RTP range restricted to provider requirements
Regular Asterisk updates
```

AMI should not be publicly exposed.

Recommended AMI bind address:

```ini
bindaddr = 127.0.0.1
```

If AI Bridge is on a separate VM and needs AMI, restrict AMI by firewall to AI Bridge IP only.

---

## 10. Production Database Migration Plan

Current POC uses SQLite:

```text
/opt/ai-support-agent/data/dashboard.db
```

Production should use PostgreSQL.

### 10.1 Production Tables

Recommended PostgreSQL schemas/tables:

```text
users
sessions
calls
quality_reviews
settings
audit_logs
security_events
rate_limits
prompt_versions
memory_events, future
webex_call_records, future
```

### 10.2 Migration Steps

```text
1. Create PostgreSQL VM.
2. Create ai_support database.
3. Create ai_support_app user.
4. Add SQLAlchemy or psycopg integration to app.
5. Export SQLite data.
6. Import into PostgreSQL.
7. Test dashboard.
8. Test bridge logging.
9. Freeze POC SQLite writes.
10. Cut over production services.
```

Recommended approach:

```text
Use SQLAlchemy to make database backend configurable.
Avoid raw sqlite3 calls long-term.
```

---

## 11. High Availability Design

### 11.1 Initial Production

Initial production can run with one VM per role:

```text
1 Asterisk
1 AI Bridge
1 Dashboard
1 PostgreSQL
1 Ticketing
1 Monitoring/Backup
```

This is simple and production-ready for a controlled first rollout, but not fully HA.

---

### 11.2 HA Production

For higher availability:

```text
2 Asterisk VMs
2 AI Bridge VMs
2 Dashboard VMs
2 PostgreSQL VMs, primary + standby
2 Ticketing app VMs, if supported
1 or 2 Monitoring/Backup VMs
```

HA diagram:

```text
SIP Provider
   │
   ├── Asterisk-01
   └── Asterisk-02
          │
          ├── AI-Bridge-01
          └── AI-Bridge-02
                 │
                 └── PostgreSQL Primary
                         │
                         └── PostgreSQL Standby

Dashboard-01 / Dashboard-02
        │
        └── PostgreSQL Primary
```

### 11.3 HA Considerations

- SIP provider must support failover routing.
- AI bridge active call state is local to each bridge node.
- Do not move an active call between bridge nodes mid-call.
- Dashboard can be load-balanced because sessions are server-side in database.
- PostgreSQL replication should be tested during failover.
- Recording storage should be replicated or backed up frequently.

---

## 12. Scaling Plan

### 12.1 Small Production

Expected load:

```text
Up to 10 concurrent AI calls
Up to 100–300 calls/day
Small IT support team
```

Recommended VM count:

```text
6 VMs
```

Recommended bridge setting:

```ini
MAX_CONCURRENT_CALLS=10
```

---

### 12.2 Medium Production

Expected load:

```text
10–30 concurrent AI calls
300–1000 calls/day
Multiple IT support agents
```

Recommended additions:

```text
Second AI Bridge VM
Second Asterisk VM or standby Asterisk
PostgreSQL standby
Dedicated recording storage
```

Recommended bridge setting per bridge node:

```ini
MAX_CONCURRENT_CALLS=15
```

Total capacity:

```text
2 bridge nodes x 15 calls = 30 concurrent calls
```

---

### 12.3 Large Production

Expected load:

```text
30–100 concurrent AI calls
1000+ calls/day
24x7 operation
```

Recommended additions:

```text
Multiple Asterisk nodes
Multiple AI Bridge nodes
Dedicated PostgreSQL cluster
Dedicated ticketing cluster
Object storage for recordings
Central logging
Full monitoring and alerting
```

---

## 13. Webex Calling Integration Design

Customer appears to use Webex Calling, not Webex Contact Center.

Production goal:

```text
Import Webex Calling completed call stats into dashboard.
```

Expected data:

```text
Call start time
Call end time
Duration
Caller
Called number
Direction
Answered / missed status
Location
Agent number
```

Recommended architecture:

```text
Webex Calling Detailed Call History API
        ↓
webex_sync.py scheduled job
        ↓
PostgreSQL webex_call_records table
        ↓
Dashboard Webex Stats page
```

Notes:

- Webex Calling stats are normally historical/completed-call records, not live contact-center queue status.
- Webex Contact Center APIs are required for true live queue and agent state if the customer later adopts Webex Contact Center.

---

## 14. Ticketing System Production Direction

### 14.1 Short Term

Keep Zammad until replacement is approved.

### 14.2 Recommended Replacement

Use Frappe Helpdesk for modern helpdesk UI.

Reasons:

```text
Modern interface
Ticket portal
SLA support
Automation
Knowledge base
API-friendly
Better customer-facing experience than Zammad for this use case
```

### 14.3 Alternative

Use GLPI if the customer wants ITSM + asset management.

Choose GLPI if customer priorities are:

```text
CMDB
Asset inventory
License management
ITIL workflows
Hardware lifecycle management
```

---

## 15. Backup Plan

### 15.1 Daily Backups

Back up:

```text
PostgreSQL database
Ticketing database
Ticketing attachments
Asterisk configuration
AI app repository
.env secrets, stored securely
Recordings
Dashboard branding assets
```

### 15.2 Backup Frequency

Recommended:

```text
Database: daily full + WAL/PITR if possible
Recordings: daily incremental
Config files: after each change
Ticketing attachments: daily incremental
```

### 15.3 Retention

Recommended:

```text
Daily backups: 14 days
Weekly backups: 8 weeks
Monthly backups: 12 months, if compliance requires
```

### 15.4 Backup Storage

Use at least two locations:

```text
Local backup staging
Remote object storage or backup appliance
```

Example remote options:

```text
AWS S3
Azure Blob
On-prem backup storage
SFTP backup server
```

---

## 16. Disaster Recovery Plan

### 16.1 RPO and RTO Targets

Suggested initial targets:

```text
RPO: 24 hours for POC production
RPO: 1 hour for mature production
RTO: 4 hours for POC production
RTO: 1 hour for mature production
```

### 16.2 DR Restore Order

```text
1. Restore PostgreSQL database.
2. Restore ticketing database and files.
3. Restore Asterisk configuration.
4. Restore AI bridge app and .env.
5. Restore dashboard app and branding assets.
6. Restore recordings if required.
7. Validate services.
8. Make test call.
9. Validate ticket creation.
10. Validate dashboard.
```

---

## 17. Monitoring and Alerting

### 17.1 Service Alerts

Alert if:

```text
Asterisk service down
AI bridge service down
Dashboard service down
PostgreSQL down
Ticketing system down
Port 8765 not listening
Dashboard port 443/8090 not reachable
OpenAI API failures increase
Ticket creation failures increase
Disk usage over 80%
Recording partition over 80%
Repeated verification failures
Repeated dashboard login failures
```

### 17.2 Call Quality Alerts

Track:

```text
Call count
Failed calls
Rejected calls
Verification blocked calls
Average duration
Calls with ticket creation failure
Calls with OpenAI response failure
Calls with transfer failure
```

---

## 18. Production Cutover Plan

### 18.1 Pre-Cutover Checklist

```text
[ ] All VMs provisioned.
[ ] Firewall rules applied.
[ ] Asterisk SIP trunk registered.
[ ] AI bridge service running.
[ ] Dashboard service running behind HTTPS.
[ ] PostgreSQL installed and reachable.
[ ] Ticketing system reachable.
[ ] OpenAI API key configured.
[ ] Dashboard secret configured.
[ ] users.csv or employee identity source finalized.
[ ] Security rate limits enabled.
[ ] Backup jobs configured.
[ ] Monitoring alerts configured.
[ ] Test call completed.
[ ] Test ticket created.
[ ] Dashboard call history verified.
```

### 18.2 Cutover Steps

```text
1. Freeze POC changes.
2. Pull latest production code.
3. Validate config and Python syntax.
4. Restart services.
5. Place test calls in English and Arabic.
6. Verify ticket creation.
7. Verify dashboard health.
8. Move SIP route/DID to production Asterisk.
9. Monitor logs for first 2 hours.
10. Keep rollback route ready.
```

### 18.3 Rollback Plan

```text
1. Re-route DID/SIP trunk to old POC server or fallback IT support number.
2. Stop production AI bridge if unstable.
3. Preserve logs.
4. Export failed call records.
5. Fix and retest before next cutover.
```

---

## 19. Recommended Production VM Summary

### First Production Deployment

```text
VM-01 Asterisk PBX
CPU: 4 vCPU
RAM: 8 GB
Disk: 100 GB SSD

VM-02 AI Bridge
CPU: 8 vCPU
RAM: 16 GB
Disk: 100 GB SSD

VM-03 Dashboard
CPU: 4 vCPU
RAM: 8 GB
Disk: 80 GB SSD

VM-04 PostgreSQL
CPU: 4 vCPU
RAM: 16 GB
Disk: 200 GB NVMe SSD

VM-05 Helpdesk / Ticketing
CPU: 8 vCPU
RAM: 16 GB
Disk: 200 GB SSD

VM-06 Monitoring / Backup
CPU: 4 vCPU
RAM: 8 GB
Disk: 300 GB SSD
```

### HA Production Deployment

```text
2 x Asterisk VMs
2 x AI Bridge VMs
2 x Dashboard VMs
2 x PostgreSQL VMs, primary + standby
2 x Ticketing app VMs, if supported
1–2 x Monitoring / Backup VMs
Shared or replicated recording storage
```

---

## 20. Final Recommendation

For the first customer production deployment, use the **6-VM standard production layout**:

```text
Asterisk
AI Bridge
Dashboard
PostgreSQL
Helpdesk
Monitoring/Backup
```

This gives the best balance of:

```text
Security
Maintainability
Troubleshooting simplicity
Future scalability
Clear service ownership
```

After customer adoption and call volume increase, move to the HA design with multiple Asterisk and AI bridge nodes.

---

## 21. Reference Notes Used for Planning

- Asterisk capacity depends heavily on concurrent calls, codecs, transcoding, conferencing, DSP, echo cancellation, and dialplan processing.
- PostgreSQL production deployment should use a modern supported Unix/Linux platform and proper storage/backups.
- Frappe production deployment uses multiple services such as frontend, backend, websocket, workers, scheduler, Redis, and database.
- GLPI requires a web server, PHP, and database, and high-traffic GLPI deployments benefit from separated web and SQL layers.
