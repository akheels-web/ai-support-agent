# AI IT Support Agent - Project Knowledge & Architecture Memory

## 1. Project Overview
AI voice assistant ("Arif") serving IT Support for National Finance (bilingual English/Arabic).
Integrates Asterisk PBX (via WebSocket/AMI) with OpenAI Realtime API (`gpt-realtime`), verifying callers from `data/users.csv`, managing tickets in Frappe Helpdesk (or Zammad), and presenting telemetry in a FastAPI dashboard (:8090).

## 2. Telephony & Media Flow
- Asterisk routes audio to WebSocket at `127.0.0.1:8765` (`app/openai_realtime_bridge.py`).
- Audio format: `audio/pcmu` (8kHz G.711 u-law).
- Full-duplex conversational audio with server VAD barge-in (`interrupt_response: True`).
- Multi-queue Asterisk AMI redirection:
  - `7001`: Standard IT L1 Support Queue
  - `7002`: Executive & VIP Concierge Queue (CEO, CFO, C-Suite)
  - `7003`: Sev-1 Emergency & Outage Incident Queue

## 3. Ticketing Architecture (Frappe Helpdesk Primary)
- Modular provider framework in `app/ticketing/`:
  - `base.py`: Abstract Base Class `BaseTicketingProvider`.
  - `frappe_provider.py`: Frappe Helpdesk (`HD Ticket`) & ERPNext (`Issue`) client. Handles SLA mapping, employee asset linkage (`lookup_assets`), and custom telephony metadata.
  - `zammad_provider.py`: Refactored Zammad client with accurate caller name and custom metadata.
  - `__init__.py`: Factory client resolver driven by `TICKETING_SYSTEM` in `.env`.
- Resolved tickets created for first-contact resolution deflection metrics (`record_resolution`).
- Emergency P1 Critical tickets created automatically on Sev-1 escalation (`escalate_emergency`).

## 4. Caller Identity & Escalation Engine
- `data/users.csv`: Stores `employee_id,name,aliases,email,phone,department,vip,role,tier`.
  - Tiers: `P0_EXECUTIVE`, `P1_VIP`, `STANDARD`.
- `app/verify.py`:
  - `lookup_caller_by_phone`: Instant Caller-ID (CLI) pre-identification for CEO and CFO, fast-tracking to executive concierge.
  - `normalize_digits`: Converts spoken English/Arabic numbers to digits.
- `knowledge_base/*.md`: Ingested into memory on startup and queried dynamically via `lookup_knowledge_base`.

## 5. Security & Operations
- `app/security_guard.py`: SQLite rate limiting on phone calls and verification attempts.
- `dashboard/app.py`:
  - Per-user random salt password hashing (`salt$hash`) with legacy static-salt backward compatibility.
  - Telemetry cards for total calls, AI deflected calls, emergency escalations, and VIP calls.
- Dynamic `BASE_DIR` paths used across all modules. Legacy `app/call_db.py` purged.
