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
