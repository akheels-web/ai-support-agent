# Project Roadmap — AI IT Support Agent ("Arif")

This document outlines upcoming strategic enhancements and future feature phases for the National Finance AI Support Agent.

---

## 1. Upcoming Phases

### Phase 4: Non-IT Inquiry Routing & Customer Service Bridge (Planned)
- **Objective**: Handle accidental calls from customers seeking non-IT assistance (e.g., personal loans, vehicle financing, credit cards, account balances, or branch locations).
- **Proposed Flow**:
  1. Detect non-IT intent via NLP classification (e.g. keywords: *"loan"*, *"finance application"*, *"credit card"*, *"interest rate"*, *"قرض"*, *"تمويل"*).
  2. Arif politely explains:
     - *English*: "This line is dedicated to National Finance Internal IT Support. To assist you with loans and banking services, I can connect you to our Customer Care team."
     - *Arabic*: "هذا الخط مخصص للدعم الفني وتقنية المعلومات. للمساعدة في خدمات التمويل والقروض، يسرني تحويلك لخدمة العملاء."
  3. Transfer call to General Customer Care Queue / SBC route (e.g. Extension `8000` or External Toll-Free).
  4. Log non-IT deflection telemetry in the dashboard for call volume auditing.

### Phase 5: Voice Biometrics & Instant Speaker Verification
- Passive voiceprint verification alongside Caller-ID (CLI) matching to enable zero-friction, passwordless verification for high-risk IT operations.

### Phase 6: Enterprise Active Directory (AD) Self-Service Account Unlock & Password Reset (Opt-In Security Policy)
- **Objective**: Provide automated, zero-touch unlocking of locked Windows Active Directory domain accounts (`lockoutTime = 0`) directly via Voice AI to deflect up to 60% of routine Service Desk calls.
- **Enterprise Objections & Security Governance**:
  - *Primary InfoSec Objection*: Risk of unauthorized account unlock through social engineering, spoofed Caller-ID, or unauthorized third-party callers.
  - *Mandatory Safeguards & Mitigation Architecture*:
    1. **Out-of-Band (OOB) Step-Up MFA Challenge**: Arif will *never* unlock an account solely on verbal name/employee ID verification. The system generates a cryptographic 6-digit Time-based OTP sent via SMS to the verified mobile phone or corporate email registered in the employee's Active Directory object (`mobile` or `mail`). The caller must recite or enter the OTP via keypad DTMF to proceed.
    2. **Strict Frequency & Rate Limiting**: Maximum 1 automated account unlock per employee within a rolling 24-hour window. Subsequent lockouts within the same day mandate live human engineer triage to investigate potential brute-force or credential-stuffing attacks.
    3. **SIEM & Tamper-Evident Audit Logging**: Every unlock operation writes an immutable cryptographic record to `audit_logs` (with SHA-256 hash chaining) capturing `employee_id`, caller CLI, timestamp, and Asterisk channel ID, broadcastable to corporate SIEM (Splunk / QRadar).
    4. **Air-Gapped Opt-In Toggle (`ENABLE_AD_SELF_SERVICE_UNLOCK`)**:
       - Default setting: `ENABLE_AD_SELF_SERVICE_UNLOCK=false`.
       - The feature remains completely disabled at the code level until National Finance IT Security & Compliance formally review the workflow, verify SMS gateway integration, and approve the activation flag in `.env`.

### Phase 7: WhatsApp, Email & Omnichannel Ticket Updates
- Automatic WhatsApp / SMS notification sent to the caller upon ticket creation and SLA resolution with live tracking link in Frappe Helpdesk.
- **Email Notifications Engine**: Integration of an SMTP/email engine (via `fastapi-mail` or `smtplib` linked to Office 365 / Exchange) to dispatch automated dashboard notifications for Sev-1 Emergency Escalations, daily PDF report generation, and new admin account provisioning.

### Phase 8: Enterprise Security Hardening & Zero-Trust Governance (Future)
- **8.1 Multi-Factor Authentication (MFA / 2FA) for Operations Dashboard**:
  - Time-based One-Time Password (TOTP via RFC 6238 / `pyotp`) for administrator and supervisor logins.
  - QR-code based enrollment compatible with Google Authenticator, Microsoft Authenticator, and 1Password.
  - Encrypted emergency single-use backup recovery codes stored in the `users` table.
- **8.2 PII Encryption-at-Rest (Application-Level AES-256-GCM / `pgcrypto`)**:
  - Field-level encryption for sensitive personally identifiable information (`phone`, `email`, `caller_name`, `transcripts`) in PostgreSQL 16.
  - Key rotation pipeline supporting integration with enterprise Key Management Systems (KMS) or HashiCorp Vault.
- **8.3 Telephony Cryptographic Anti-Spoofing (STIR/SHAKEN & ANI Validation)**:
  - Carrier-grade validation of incoming SIP headers (`P-Asserted-Identity`, `Remote-Party-ID`, and STIR/SHAKEN attestation) to detect and reject spoofed caller IDs before PBX queue routing.
  - In-band acoustic voice biometrics (Phase 5) integration for high-privilege executive calls.
- **8.4 Mutual TLS (mTLS) for Private Internal Microservices**:
  - X.509 client certificate pinning and mutual TLS between Asterisk voice bridge, private Frappe Helpdesk instances, and enterprise directory services.

### Phase 9: Enterprise Hardware Asset Correlation & CMDB Integration (Planned)
- **Objective**: Automatically correlate callers with their assigned IT hardware assets (laptops, monitors, docks, phones, accessories) during hardware replacement or damage inquiries.
- **Trigger**: When the client deploys an active enterprise IT Asset database / CMDB (e.g. ERPNext Asset DocType or external CMDB).
- **Proposed Capabilities**:
  1. Verified employee reports hardware fault (e.g. *"My laptop screen is cracked"* or *"My dock stopped charging"*).
  2. Arif invokes `lookup_employee_assets(employee_id)` to retrieve currently assigned hardware records (asset name, model, serial number, warranty status).
  3. Arif confirms device context with caller: *"I see you are assigned a Dell Latitude 5440 (Serial: SN-98213). Is this the device experiencing the issue?"*.
  4. Automatically attaches the exact asset ID and serial number to the generated Helpdesk hardware request ticket.
  5. Enforces Department Manager approval workflow before IT dispatch.

### Phase 10: Unified P0 + P1 VIP Automatic Human Transfer Policy (Pending Client Decision)
- **Objective**: Provide a configurable automated routing policy for VIP callers, determining whether all VIP tiers (Directors, Dept Heads, and C-Suite) bypass AI troubleshooting entirely and transfer immediately to human agents on Cisco Webex Calling.
- **Current Baseline**:
  - `P0_EXECUTIVE` (CEO, CFO, C-Suite): **Instant Automatic Bypass & Transfer** via CLI phone match to Webex Executive Concierge (Extension `8002`).
  - `P1_VIP` (Directors, Infrastructure Leads, HR Directors): **Priority-Assisted AI Flow** (priority greeting, P1 High SLA ticket creation, on-demand fast-track human transfer).
- **Client Policy Options**:
  - **Option A: Full VIP Auto-Bypass (P0 + P1)**:
    - Both P0 Executives and P1 VIPs automatically skip all AI troubleshooting questions.
    - Upon language selection, Arif immediately says: *"Welcome Mr. [Name], transferring you directly to our Priority IT Support Team right now."* and executes an AMI redirect to the Cisco Webex queue.
  - **Option B: Dual-Tier Dedicated Webex Queues**:
    - `P0_EXECUTIVE` $\rightarrow$ Routes to Webex Senior Executive Concierge (Extension `8002`, immediate priority ring to Senior IT Engineers).
    - `P1_VIP` $\rightarrow$ Routes to Webex Priority Management Queue (Extension `8004`, priority queue jump ahead of standard L1 tickets).
  - **Option C: Hybrid Default (Current)**:
    - P0 auto-transfers immediately.
    - P1 receives rapid AI assistance with instant one-phrase transfer (*"transfer me to an agent"*).
- **Implementation Mechanism**:
  - Controlled via single configuration flag in `.env`: `VIP_AUTO_TRANSFER_ALL=true|false`.
  - Enables instant toggling without codebase refactoring once the client confirms their preferred protocol.

### Phase 11: Enterprise Redis Distributed Cache, Pub/Sub & High-Throughput State Engine (Planned Scale-Out)
- **Objective**: Introduce Redis 7+ / Valkey as a distributed, high-performance in-memory state engine for multi-node voice edge clustering and sub-millisecond live dashboard telemetry.
- **Architectural Triggers & Use Cases**:
  1. **Multi-Node Voice Edge Clustering**:
     - Centralizes session states, employee verification rate limits, and global channel concurrency counters across multiple Asterisk / Voice Bridge VMs deployed behind a SIP Load Balancer (Kamailio / Cisco CUBE).
  2. **Sub-Second Event-Driven Cache Invalidation (Pub/Sub)**:
     - Replaces the 15-second TTL cache with instant Redis Pub/Sub event broadcasting (`PUBLISH caller_updated 1002`). When an administrator offboards an employee or adds a new playbook in the dashboard, all voice bridge workers across all VMs invalidate their cache in under 5 milliseconds.
  3. **Real-Time Live Call Telemetry & Transcript Streaming**:
     - Voice bridge publishes live call status changes and incremental transcript chunks to Redis Pub/Sub (`live_calls_channel`).
     - FastAPI dashboard streams live updates to supervisors' browsers via WebSockets / Server-Sent Events (SSE), showing real-time conversational speech bubbles as callers speak, with zero database polling overhead.
  4. **Distributed Atomic Rate Limiting**:
     - Moves sliding-window call frequency checks (`CALLS_PER_NUMBER_LIMIT`) and brute-force verification protection to Redis Sorted Sets (`ZADD / ZREMRANGEBYSCORE`), capable of 120,000 ops/sec to protect PostgreSQL during storm surges.
  5. **Asynchronous Background Task Broker**:
     - Serves as the message broker for Celery / RQ workers handling WhatsApp/SMS delivery (Phase 7), bulk Active Directory synchronizations (10,000+ users), and automated executive report distribution.

### Phase 12: AI Agent Long-Term Memory (Contextual History) via PostgreSQL
- **Objective**: Provide Arif with contextual awareness of a caller's previous interactions and unresolved tickets immediately upon call connection, creating a highly personalized and intelligent IT helpdesk experience.
- **Architecture (No External Vector DB Required)**:
  1. **Direct Querying via Relational Database**: Since all interactions are already logged in the `calls` table and tickets in Frappe, we will query PostgreSQL directly instead of relying on external memory tools. This guarantees 100% accuracy and eliminates hallucination risks associated with vector databases.
  2. **Pre-Flight Context Fetch**: Upon successful caller verification (`lookup_caller_by_phone`), a lightweight background SQL query fetches the caller's last 3 recent call summaries and any active Frappe tickets.
  3. **Prompt Injection**: The retrieved history is injected as a "Context Summary" block directly into the OpenAI Realtime API `session.update` system prompt.
  4. **Proactive Assistance**: Arif uses this injected data to greet the user contextually: *"Hi [Name], I see you called yesterday about Outlook (Ticket HD-2026-0012). Did that get resolved, or are you calling about something else?"*

### Phase 13: LiveKit WebRTC Realtime Stack & Multi-Channel Voice Expansion (Evaluated Architecture)
- **Objective**: Extend AI Support Agent "Arif" beyond traditional PSTN/telephony desk phones into browser-based and mobile omni-channel environments using the open-source [LiveKit WebRTC Stack](https://github.com/livekit/livekit).
- **Architectural Motivation**:
  - The current production architecture uses Asterisk PBX + custom TCP AudioSocket streaming (`app/openai_realtime_bridge.py`), which is optimized for standard telecom desk phones over Cisco CUBE.
  - LiveKit introduces a modern, distributed WebRTC SFU and the `livekit-agents` framework, opening up high-fidelity digital channels and modular AI orchestration.
- **Key Capabilities & Strategic Benefits**:
  1. **In-Browser Web Voice AI Widget ("Talk to Arif")**:
     - Embed a zero-friction WebRTC voice button directly inside the **Frappe Helpdesk web portal** (`nfticketing.nfc.co.om`), IT Dashboard, or internal corporate intranet.
     - Remote employees on laptops can communicate directly with Arif using high-fidelity Opus audio (48kHz studio quality) with echo cancellation—no desk phone or telephone extension required.
     - The identical backend agent intelligence, Active Directory verification, and ticket creation workflows serve both web users and telephone callers.
  2. **Simplified Audio Pipeline & Native Interruption Handling**:
     - Replaces low-level socket byte framing, custom 8kHz u-law $\leftrightarrow$ 24kHz PCM16 resampling, and manual audio buffering with LiveKit's optimized, battle-tested C/Rust audio pipeline.
     - Native Turn Detection, Silero Voice Activity Detection (VAD), and barge-in / speech-interruption management out of the box.
  3. **Multi-Model Flexibility (Cost & Compliance Optimization)**:
     - Allows hot-swapping between:
       - **OpenAI Realtime API (`gpt-realtime`)** (current flagship mode).
       - **Modular Pipeline**: Deepgram Nova-3 (STT) + Claude 3.5 / LLaMA 3.3 (LLM) + Cartesia / ElevenLabs (TTS).
       - **On-Premise / Local Inference**: Ability to run self-hosted local speech and LLM models if National Finance mandates air-gapped data residency in future compliance audits.
  4. **Unified Telephony Bridge via LiveKit SIP (`livekit/sip`)**:
     - Ability to bridge incoming calls from Cisco CUBE directly into WebRTC rooms, creating a unified media plane where human supervisors, callers, and AI agents can participate in the same session.
- **Deployment Strategy**:
  - **Phase 1 (Active Production Launch)**: Maintain the current Asterisk PBX + Cisco CUBE SIP trunk (verified, stable, with native Music on Hold and 3.8ms latency).
  - **Phase 13 (Post-Launch Expansion)**: Deploy `livekit-server` and `livekit-agents` alongside the existing infrastructure to power web/mobile voice widgets without disrupting existing telephony trunking.

---


## 2. Completed Milestones
- [x] Full-duplex conversational audio with 40ms frame pacing and Asterisk DSP denoise.
- [x] Executive (CEO/CFO) Caller-ID pre-identification and VIP concierge routing.
- [x] Emergency Sev-1 incident detection with auto-generated P1 tickets and queue 7003 escalation.
- [x] Frappe Helpdesk modular provider integration with SLA policies.
- [x] Manager approval workflow for hardware requests in Frappe Helpdesk.
- [x] PostgreSQL 16 enterprise database migration with connection pooling.
- [x] AI Anti-Hallucination and ghost ticket prevention guardrails.
- [x] Zero default credentials and first-run `/setup` administrator wizard.
- [x] Immediate session invalidation on password change and administrative reset.
- [x] Automated recording retention pruning policy (`recording_retention_days`).
- [x] Strict SQL identifier whitelisting and CSP/security headers middleware.
- [x] Compound bilingual Arabic and English digit normalization (`normalize_digits`).
- [x] Digit-by-digit ticket number recital and repetition (`repeat_ticket_number`).
- [x] Scheduled callback ticketing in Frappe Helpdesk (`request_callback`).
- [x] Graceful Asterisk AMI transfer failure recovery (eliminating dead-air drops).
- [x] Active Directory (AD/LDAP) Enterprise Sync with Arabic phonetic alias preservation and automated offboarding.
- [x] In-Band DTMF Telephone Keypad fallback for noisy environment data entry.
- [x] Bilingual Semantic Knowledge Base Retrieval with cross-process zero-downtime cache reflection.
- [x] Cisco Webex Screen-Pop Integration via Asterisk AMI `Setvar` context injection.
- [x] Enterprise Audio Engine with Band-Limited Resampling, Soft-Clipping, and DC Offset Filtering (`app/audio_engine.py`).
- [x] Dynamic VAD Noise Floor Adaptation and 120ms Jitter Pre-Buffering.
- [x] Response Watchdog Timer (5.0s deadlock recovery) and `response.cancelled` state recovery.
- [x] Asterisk Attended Transfer Recovery for unanswered agent transfers (`recover_channel_to_ai`).
- [x] AI Provider Outage Graceful Fallback with advisory chime and Frappe callback ticketing.
- [x] Post-generation Tool Call Hallucination Validator (`validate_tool_call`).
