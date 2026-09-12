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

### Phase 6: Active Directory / Azure AD (Entra ID) Direct Self-Service
- Integration with Microsoft Graph API / LDAP to allow Arif to trigger secure, verified self-service password resets and account unlocks automatically after SMS/MFA challenge verification.

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

