# AI IT Support Agent - Project Knowledge & Architecture Memory

## 1. Project Overview
AI voice assistant ("Arif") serving IT Support for National Finance (bilingual English/Arabic).
Integrates Asterisk PBX (via WebSocket/AMI) with OpenAI Realtime API (`gpt-realtime`), verifying callers from `data/users.csv`, managing tickets in Frappe Helpdesk, and presenting telemetry in a FastAPI dashboard (:8090).

## 2. Telephony & Media Flow
- Asterisk routes audio to WebSocket at `127.0.0.1:8765` (`app/openai_realtime_bridge.py`).
- Audio format: `audio/pcmu` (8kHz G.711 u-law).
- Full-duplex conversational audio with server VAD barge-in (`interrupt_response: True`).
- Multi-queue Asterisk AMI redirection:
  - `7001`: Standard IT L1 Support Queue
  - `7002`: Executive & VIP Concierge Queue (CEO, CFO, C-Suite)
  - `7003`: Sev-1 Emergency & Outage Incident Queue

## 3. Ticketing Architecture (Frappe Helpdesk)
- Modular provider framework in `app/ticketing/`:
  - `base.py`: Abstract Base Class `BaseTicketingProvider` for future extensibility (e.g. GLPI if needed).
  - `frappe_provider.py`: Dedicated Frappe Helpdesk (`HD Ticket`) & ERPNext (`Issue`) client. Handles SLA mapping, employee asset linkage (`lookup_assets`), custom telephony metadata, and **Department Manager approval workflow** for physical hardware requests (`workflow_state: Pending Approval`, `custom_approval_status: Pending Manager Approval`).
  - `__init__.py`: Singleton resolver `get_ticketing_client()` instantiating `FrappeProvider`.
- Resolved tickets created for first-contact resolution deflection metrics (`record_resolution`).
- Emergency P1 Critical tickets created automatically on Sev-1 escalation (`escalate_emergency`).

## 4. Caller Identity, Escalation & AI Guardrails
- `data/users.csv`: Stores `employee_id,name,aliases,email,phone,department,vip,role,tier`.
  - Tiers: `P0_EXECUTIVE`, `P1_VIP`, `STANDARD`.
- `app/verify.py`:
  - `lookup_caller_by_phone`: Instant Caller-ID (CLI) pre-identification for CEO and CFO, fast-tracking to executive concierge.
  - `normalize_digits`: Converts spoken English/Arabic numbers to digits.
- `knowledge_base/*.md`: Ingested into memory on startup and queried dynamically via `lookup_knowledge_base`.
- Deterministic AI Guardrails in `app/openai_realtime_bridge.py`:
  - **Verification Gate**: Caller must be verified before ticket creation.
  - **Quality Gate**: Description must be >= 10 characters and technical (blocks "hi", "test", "issue").
  - **Scope Gate**: Rejects non-IT inquiries (loans, vehicle finance, interest rates, credit cards) without ticket generation.
  - **Duplicate Prevention**: Rejects secondary ticket creation in the same call session.
  - **Anti-Hallucination Constraints**: Strict prompt rules forbidding false ticket generation and direct password claim assertions.

## 5. Enterprise Database & Security
- `app/db.py`: Enterprise database client supporting PostgreSQL 16 via connection pooling (`psycopg_pool` / `psycopg2`) with automatic SQLite WAL fallback for local development.
  - Composite indexes on `calls` (`call_id`, `start_time, status`, `caller_number`, `tier`), `security_events`, `sessions`, and `audit_logs`.
  - Transparent SQL parameter normalization (`?` to `%s`) across engines.
  - Atomic rate limiting eliminating concurrency race conditions under multi-call load.
- `app/call_logger.py` & `app/security_guard.py`: Refactored to use `app.db` connection pooling.
- `dashboard/app.py`:
  - Per-user random salt password hashing (`salt$hash`) with legacy static-salt backward compatibility.
  - Telemetry cards for total calls, AI deflected calls, emergency escalations, and VIP calls.
  - Full PostgreSQL 16 connectivity via `app.db.get_db()`.
- Dynamic `BASE_DIR` paths used across all modules. Legacy `app/call_db.py` purged.

## 6. Executive Operations Dashboard (Shadcn/UI & Analytics)
- `dashboard/app.py`: FastAPI application serving port 8090.
- Modular Jinja2 templates in `dashboard/templates/` (`base.html`, `dashboard.html`, `calls.html`, `active_calls.html`, `failed_calls.html`, `recordings.html`, `health.html`, `security_events.html`, `settings.html`, `prompts.html`, `users.html`, `login.html`).
- Design System (`dashboard/static/css/shadcn.css`):
  - HSL tokens matching Shadcn/UI specifications with Corporate Light Mode default and seamless Dark Mode toggle.
  - Inter & JetBrains Mono typography, card containers, badge pills, and Lucide SVG icons.
- Visual Analytics (`dashboard/static/js/charts.js`):
  - 24-hour Call Volume & Autonomous Deflection Trend (multi-line area with smooth spline bezier curves).
  - First-Contact Deflection & Resolution Breakdown (interactive doughnut chart).
  - Queue Distribution Bar Chart (L1 7001 vs VIP Concierge 7002 vs Sev-1 Emergency 7003).
- Data Tables & Telemetry Engine (`dashboard/static/js/app.js`):
  - Real-time client search, column sorting, and status pill badges.
  - Slide-out Call Inspection Drawer for full transcript, employee profile, and audio playback.
  - Dynamic 30-second auto-refresh for summary stats and 10-second polling for active calls.
- Executive PDF Reporting (`dashboard/static/js/pdf-report.js`):
  - One-click executive shift summary export with National Finance & TCT branding, KPI metric blocks, and chart snapshots.
- REST API Endpoints:
  - `GET /api/dashboard/stats`: Real-time aggregate counters.
  - `GET /api/dashboard/chart-data`: Time-series 24h buckets and queue breakdowns.
  - `GET /api/active-calls/data`: In-progress live channels.
  - `GET /api/calls/{call_id}`: Single call inspection payload.

## 7. Security Hardening & Zero-Default Architecture
- **Zero Default Passwords**: Removed hardcoded credentials (`admin123`/`user123`). If no administrator exists, the system automatically redirects to `/setup` (First-Time Administrator Setup Wizard) which permanently locks once an admin is created. Automated bootstrapping supported via `INITIAL_ADMIN_PASSWORD`.
- **Session Revocation**: Password changes and admin resets invalidate all active sessions for that user (`DELETE FROM sessions WHERE username=?`), terminating stale or hijacked sessions.
- **SQL Identifier Sanitization**: Added strict regex whitelisting (`^[a-zA-Z0-9_]+$`) in `app/db.py:_ensure_column` eliminating SQL injection vectors.
- **Automated Retention Pruning**: `enforce_recording_retention()` periodically scans telephony storage and prunes `.wav` audio files older than `recording_retention_days` (default 30 days) to prevent disk exhaustion.
- **Security Headers Middleware**: Injected `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`, and strict `Content-Security-Policy`.
- **Sanitized Diagnostics**: `/health` hides raw daemon names and internal paths, reporting standardized operational state.
- **WSS / TLS Media Channel**: Added native SSLContext support to `app/openai_realtime_bridge.py` (`ASTERISK_WS_SSL_CERT` and `ASTERISK_WS_SSL_KEY`), allowing encrypted `wss://` on port 8765 while maintaining loopback efficiency for colocated deployments.
- **TLS AMI Channel**: Added TLS socket wrapping to `app/transfer.py` (`ASTERISK_AMI_TLS`) for secure Asterisk Manager Interface actions over port 5038/5039.
- **Cryptographic Audit Log Hash Chaining**: `audit_logs` table links rows via `prev_hash` and `record_hash` computing an append-only SHA-256 tamper-evident ledger, verifiable via `verify_audit_trail()`.
- **Global Route Rate Limiting**: Added sliding-window in-memory IP rate limiter (120 req/min per client IP) across all dashboard pages and APIs in `dashboard/app.py`.
- **Enforced Host Isolation**: Configured dashboard daemon to bind strictly to `127.0.0.1` (`DASHBOARD_HOST`), isolating service behind reverse proxy.

## 8. Voice AI Resilience & Caller Experience Enhancements
- **Digit-by-Digit Recital & Repetition (`repeat_ticket_number`)**:
  - Automatically recites ticket reference numbers slowly and spaced digit-by-digit (`H D 2 0 2 6 0 0 1 2`).
  - Dedicated `repeat_ticket_number` tool allows callers to ask Arif to repeat the reference number at any point without re-triggering ticket creation.
- **Scheduled Callbacks (`request_callback`)**:
  - Automatically logs a callback request ticket in Frappe Helpdesk when callers prefer not to wait on hold or when transfer queues fail.
  - Captures preferred callback time ("ASAP", "within 1 hour", "morning"), caller contact phone, and issue context.
- **Graceful Asterisk Transfer Recovery**:
  - Eliminates dead air drops: if Asterisk AMI queue transfer fails or agents are unavailable, Arif sincerely apologizes, confirms the ticket reference number, and immediately offers a scheduled callback.
- **Advanced Bilingual Compound Digit Normalization (`app/verify.py`)**:
  - Parses single digits (0–9), colloquial Gulf/Omani Arabic teens (`أحد عشر`, `احداعش`, `حداعش`, `اثنعش`, `طنعش`, `تلطعش`, `اربعطعش`, `خمسطعش`, `ستطعش`, `سبعطعش`, `ثمانطعش`, `تسعطعش`), tens (20–90), hundreds (100–900), and thousands.
  - Strips Arabic conjunction prefix `و` (`وعشرين` -> `20`, `واثنين` -> `2`) and English `and`.
  - Performs composite summation (`ألف واثنين` -> `1002`, `one thousand and two` -> `1002`, `خمسة وعشرين` -> `25`, `twenty five` -> `25`) or direct concatenation for digit-by-digit dictation (`واحد صفر صفر اثنين` -> `1002`).

## 9. Strict Issue Resolution & Lifecycle Tracking Protocols
- **Mandatory Outcome Verification Protocol**:
  - After delivering each single diagnostic instruction from `lookup_knowledge_base`, Arif explicitly mandates caller verification: *"Did that resolve the issue for you?"* / *"هل تم حل المشكلة معك الآن؟"*.
  - **Outcome A (Resolved)**: Immediately calls `record_resolution` to generate a `Resolved` ticket in Frappe Helpdesk, logs First Contact Resolution (`ai_deflected=1`) telemetry, and recites ticket digit-by-digit.
  - **Outcome B (Unresolved)**: Compiles all attempted steps, symptoms, and error messages into `create_ticket` with status `Open`, recites ticket number, and offers transfer or callback.
- **Existing Ticket Tracking Tool (`check_ticket_status`)**:
  - Allows verified callers to track the live status, manager approval progress, and resolution notes of any existing IT ticket.
  - Normalizes spoken space formats (e.g. `HD 2026 0012` -> `HD-2026-0012`) and queries Frappe with fallback search.
- **Hardware Request & Approval Governance**:
  - Hardware inquiries (damaged equipment, replacement requests) automatically route to `create_ticket(group="Hardware Request")`, creating a ticket with `Pending Manager Approval`.
  - Future CMDB hardware asset lookup captured in `roadmap.md` (Phase 9).

## 10. Cisco Webex Integration & Call Transfer Architecture
- Comprehensive enterprise guide created in `CISCO_WEBEX_DOCUMENTATION.md`.
- Documents 4 deployment models:
  - **Model 1**: Webex Calling with Local Gateway (Cisco CUBE IOS-XE via SIP trunk).
  - **Model 2**: Cisco Unified Communications Manager (CUCM CallManager on-premise cluster).
  - **Model 3**: Pure Cloud Webex Calling (Premises-to-Cloud via mTLS & SRTP).
  - **Model 4**: Carrier PSTN / E.164 DID outdial (Immediate PoC / fallback).
- Voice AI Bridge (`app/transfer.py`) passes caller context (`AI_CALLER_NAME`, `AI_EMPLOYEE_ID`, `AI_TIER`, `AI_REASON`, `AI_TICKET_NUMBER`) onto Asterisk channel via AMI `Setvar`, enabling screen-pop on human agents' Cisco Webex desktop apps and desk phones.
- **VIP Routing Policy (P0 + P1)**: P0 (CEO/CFO) auto-bypasses AI diagnostics directly to Webex queue `8002`. P1 (Directors) receives priority-assisted AI service. Unified auto-bypass policy (`VIP_AUTO_TRANSFER_ALL`) documented in `roadmap.md` (Phase 10) for client review.

## 11. Caller Directory & Telephony VIP Roster Management
- **Enterprise Database Architecture (`callers` Table)**:
  - Supports PostgreSQL 16 and SQLite with composite indexing on `employee_id` (unique), `phone`, and `active`.
  - Automatically seeds from `data/users.csv` on initial initialization if empty.
  - Maintains strict bi-directional synchronization via `app.db.sync_callers_to_csv()`, keeping `data/users.csv` updated for file-based backups and CLI scripts.
- **Verification Engine & Offboarding Security (`app/verify.py` & `app/openai_realtime_bridge.py`)**:
  - `_load_users()` queries the database `callers` table with in-memory caching (`lru_cache`) and fallback to `users.csv`.
  - Immediate offboarding enforcement: `lookup_caller_by_phone` skips deactivated callers (`active=0`), and `verify_user` returns `{"verified": False, "reason": "account_deactivated"}`.
  - Bridge steers Arif in both Arabic & English to politely notify callers that their employee account is deactivated and directs them to contact HR/IT administration.
- **Executive Dashboard UI (`/callers`)**:
  - Live KPI metric cards: Total Registered Callers, Active & Verified, C-Suite & VIP Concierge, Offboarded/Inactive.
  - Search & filter bar (query, escalation tier, status, department).
  - One-click immediate status toggling (`Offboard` / `Activate`).
  - Full modal dialogs for onboarding new employees, editing phonetic Arabic/English aliases (`منصور الحبسي | Mansoor`), updating VIP tiers, and bulk CSV roster importing/exporting.
  - Complete integration readiness for the upcoming on-premises Active Directory (AD/LDAP) background sync worker.

## 12. Active Directory (AD / LDAP) Enterprise Sync Connector
- **Connector Architecture (`app/ad_sync.py`)**:
  - Secure LDAPS (TCP 636) and LDAP/StartTLS (TCP 389) connector built with `ldap3` and socket timeout guardrails (5s connect, 15s search).
  - Paged search streaming (`paged_search` with batch size 500) preventing Windows Server AD `LDAP_SIZELIMIT_EXCEEDED` errors when querying large enterprise user bases.
  - Decoupled asynchronous design: phone calls and Asterisk media bridge never block on domain controller queries, operating at sub-5ms latency from local database/memory caches.
- **Enterprise Safety & Offboarding Evaluation**:
  - Bitwise evaluator for `userAccountControl` (MS-SAMR): checks flag `0x0002` (`ACCOUNTDISABLE`) to automatically mark terminated staff as `active=0`.
  - Strict non-destructive upsert: **preserves all existing custom spoken Arabic and English phonetic aliases** in the database to prevent speech recognition degradation.
  - Safe against accidental directory wipe: never hard deletes missing records from misconfigured OUs.
- **Dashboard Diagnostics & Controls (`dashboard/app.py`, `/callers`, `/settings`)**:
  - REST endpoints: `POST /api/ad/test-connection` (round-trip latency, TLS handshake, and bind verification) and `POST /api/ad/sync-now` (manual on-demand sync with audit logging).
  - UI banner on `/callers` displaying live domain controller connection state, last sync timestamp, and total scanned/deactivated metrics.
  - Diagnostic modal with real-time handshake latency and canary account verification.
  - Complete configuration form in `/settings` allowing live parameter tuning and connection testing.

## 13. Pre-Flight Asterisk AMI Queue Availability & Capacity Guard
- **Pre-Flight Inspection (`app/transfer.py`)**:
  - `check_queue_availability(queue_type, extension)` connects to Asterisk AMI via `_ami_connect()` and issues `Action: QueueSummary`.
  - Maps queues dynamically: `it-support` (7001), `it-vip-exec` (7002), and `it-emergency` (7003). Emergency queues fail open.
  - Parses `LoggedIn`, `Available`, and `Callers`. If 0 agents are available or logged in, flags `available: False`.
- **Bridge Integration (`app/openai_realtime_bridge.py`)**:
  - Prior to issuing an AMI `Redirect`, `transfer_to_agent` inspects queue availability.
  - If unavailable, the transfer is prevented; Arif sincerely apologizes, confirms the ticket reference number, and immediately offers a scheduled callback (`request_callback`) instead of abandoning the caller in an endless ringing or hold music loop.

## 14. In-Band DTMF Telephone Keypad Fallback Engine
- **Keypad Digit Capture (`app/openai_realtime_bridge.py`)**:
  - In `asterisk_to_openai`, detects DTMF frames from WebSocket media stream and Asterisk dialpad signals.
  - Buffers digits in `state["dtmf_buffer"]`. Entering `#` or 4 consecutive digits triggers automated verification against the enterprise `callers` roster.
  - Dedicated tool `submit_dtmf_keypad` allows the voice model to process keypad inputs directly.
  - Protocol 15 in `SYSTEM_PROMPT` steers Arif to offer keypad entry whenever a caller is in a noisy branch, mobile car environment, or when voice recognition fails.

## 15. Bilingual Semantic Knowledge Base Retrieval & Full Conversation Transcripts
- **Semantic KB Matcher (`search_knowledge_base`)**:
  - Replaced naive substring matching with multi-factor scoring (exact match, alias match, and token-overlap scoring).
  - `KB_METADATA` provides comprehensive Arabic and English synonyms for all 14 playbooks (e.g. Wi-Fi, Outlook, slow PC, password reset, account lock, VPN, Teams, printers).
- **Full Dialogue Transcription & Auto-Summaries**:
  - Enabled `"input_audio_transcription": {"model": "whisper-1"}` in OpenAI Realtime session configuration.
  - Records chronological dialogue turns (`Caller: ...`, `Arif: ...`) into `state["transcript_lines"]`.
  - Database schema migrated: added `transcript TEXT` to `calls` table in both PostgreSQL 16 and SQLite WAL with auto-migration via `_ensure_column`.
  - On call completion, generates a structured IT summary and persists full transcript to the database.
  - Slide-out Call Inspection Drawer in `/calls` renders styled conversation bubbles with speaker avatars, timestamps, and turn counters.

## 16. Dynamic Knowledge Base Management & Zero-Downtime Telephony Reflection
- **Enterprise Database Playbook Repository (`app/db.py`)**:
  - Migrated from static markdown-only storage to `knowledge_articles` table supporting both PostgreSQL 16 and SQLite WAL with composite indexing on `article_id` (unique), `active`, and `category`.
  - Automatically seeds from `knowledge_base/*.md` on first boot with categories and rich bilingual (Arabic & English) voice trigger synonyms.
  - Bi-directional synchronization: updating or creating an article in the UI automatically dumps content to `knowledge_base/<article_id>.md` for Git version control and file-based backups.
  - CRUD & status helpers: `list_knowledge_articles()`, `get_knowledge_article()`, `save_knowledge_article()`, `toggle_knowledge_article()`, and `delete_knowledge_article()`.
- **Zero-Downtime Voice Reflection (`app/openai_realtime_bridge.py`)**:
  - `get_active_knowledge_articles()` dynamically streams active playbooks from the database with an in-memory 15-second TTL cache (`_KB_CACHE`).
  - Allows administrators to add, edit, or toggle troubleshooting playbooks in the dashboard with instant live reflection on incoming telephony calls without restarting the WebSocket bridge daemon or interrupting active calls.
  - Multi-factor semantic scoring evaluates query against article slug, title, English voice trigger tokens, Gulf/Omani Arabic voice triggers, and markdown content overlap.
- **Executive Operations UI & REST Endpoints (`dashboard/app.py`, `/knowledge`)**:
  - KPI metric cards: Total Playbooks, Active in Voice AI, Operational Categories, Inactive/Disabled.
  - Search toolbar with real-time text query, category filtering, and active/inactive status switches.
  - Full CRUD modal dialogs: Create New Playbook, Edit Playbook, and View Markdown Preview with copy button.
  - One-click active status toggle button (`POST /knowledge/toggle-status`) and deletion with tamper-evident audit logging (`audit_logs` hash chain).
  - Navigation link added under Administration in `dashboard/templates/base.html`.

