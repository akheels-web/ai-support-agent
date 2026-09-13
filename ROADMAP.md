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

### Phase 7: WhatsApp & Omnichannel Ticket Updates
- Automatic WhatsApp / SMS notification sent to the caller upon ticket creation and SLA resolution with live tracking link in Frappe Helpdesk.

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

