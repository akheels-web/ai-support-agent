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

---

## 2. Completed Milestones
- [x] Full-duplex conversational audio with 40ms frame pacing and Asterisk DSP denoise.
- [x] Executive (CEO/CFO) Caller-ID pre-identification and VIP concierge routing.
- [x] Emergency Sev-1 incident detection with auto-generated P1 tickets and queue 7003 escalation.
- [x] Frappe Helpdesk modular provider integration with SLA policies.
- [x] Manager approval workflow for hardware requests in Frappe Helpdesk.
- [x] PostgreSQL 16 enterprise database migration with connection pooling.
- [x] AI Anti-Hallucination and ghost ticket prevention guardrails.
