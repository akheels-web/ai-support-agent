# National Finance AI IT Support Agent ("Arif")
## Enterprise Call Testing & Quality Assurance Guide

**Document Version:** 2.4 (Production Grade)  
**Target System:** Asterisk PBX 20+ / Cisco Webex Calling / OpenAI Realtime Engine (`gpt-realtime`)  
**Telephony Audio Format:** 8kHz PCMU (G.711 u-law) / Full-Duplex Server VAD Barge-In  
**Ticketing Backend:** Frappe Helpdesk (`HD Ticket`) & ERPNext (`Issue`)  
**Executive Telemetry:** FastAPI Real-Time Dashboard (`:8090`)  

---

### Executive Overview for Test Operators & Stakeholders

This guide provides an exhaustive suite of **14 real-world test scenarios** for evaluating the National Finance Voice AI Assistant (**Arif**). It is designed for IT QA engineers, contact center managers, and client stakeholders to test the voice assistant across all telephony channels, softphones, mobile desk phones, and dialpads.

For each scenario, the guide specifies:
1. **Test Objective & Category** (Deflection, Transfer, VIP Fast-Track, DTMF Keypad, Bilingual Arabic/English, Hardware Governance, Emergency Sev-1).
2. **Pre-Call Setup & Test Caller Persona** (Registered Employee ID, Phone CLI, VIP Tier).
3. **Turn-by-Turn Spoken Dialogue** (Exact phrases in English and Gulf/Omani Arabic).
4. **Expected Spoken Audio Output from Arif** (Word-for-word, digit-by-digit reference delivery).
5. **Telephony & PBX Actions** (SIP signaling, Asterisk AMI redirects, queue distribution).
6. **Frappe Helpdesk Output** (Ticket ID, Status, Workflow State, Custom SLA, Manager Approval).
7. **FastAPI Operations Dashboard Telemetry** (Metric counters, conversation transcripts, inspection drawer).

---

## Registered Test Personas Reference Table

| Employee ID | Spoken Name & Arabic Aliases | Phone Number (CLI) | Department | Role | VIP Tier |
|:---:|---|---|:---:|:---:|:---:|
| **1000** | Khalid Al Harthy / خالد الحارثي / الرئيس التنفيذي | `+96899000001` | Executive | CEO | `P0_EXECUTIVE` |
| **1010** | Salim Al Maskari / سالم المسكري / المدير المالي | `+96899000002` | Finance | CFO | `P0_EXECUTIVE` |
| **1001** | Ahmed Al Balushi / أحمد البلوشي | `+96890000001` | IT | IT Director | `P1_VIP` |
| **1002** | Mohammed Akheel / محمد عقيل | `+919000000001` | IT | Software Engineer | `STANDARD` |
| **1003** | Haitham Al Sabahi / هيثم الصباحي | `+96890000003` | Finance | Accountant | `STANDARD` |
| **1008** | Ruqaiya Al Balushi / رقية البلوشي | `+96890000008` | IT | Service Desk Lead | `P1_VIP` |
| **9999** | Deactivated Employee (Offboarded) | `+96890000999` | Operations | Former Clerk | `INACTIVE (0)` |

---

## Asterisk PBX Queue Routing Reference

| Queue Number | Extension | Service Profile | Priority / Target Desk |
|:---:|:---:|---|---|
| **7001** | `7001` | Standard IT L1 Support | First-line human engineers, general hardware/software |
| **7002** | `7002` | Executive & VIP Concierge | CEO, CFO, C-Suite, IT Directors (Zero-wait queue) |
| **7003** | `7003` | Sev-1 Emergency Incident | Critical infrastructure outages, cyber alerts, core banking |

---

# Detailed Call Testing Scenarios

---

### Scenario 1: First-Contact Resolution (FCR) via Self-Service Deflection (English)
*Tests autonomous diagnostic guidance, mandatory outcome verification protocol, ticket resolution generation, and digit-by-digit recitation.*

- **Caller Persona:** Mohammed Akheel (ID: `1002`, Phone: `+919000000001`, Standard)
- **Target Issue:** Microsoft Outlook crash / freezing during startup

#### Turn-by-Turn Dialogue:
```
[PBX Connects Call]
Arif:   "Welcome to National Finance IT Support. For English please say English. للغة العربية قل عربي."

Tester: "English please."

Arif:   "Thank you for choosing English. May I please have your full name?"

Tester: "Mohammed Akheel."

Arif:   "Could you please provide your 4-digit employee ID?"

Tester: "One zero zero two."

Arif:   "Thank you Mohammed Akheel, you are verified. How can I help you today?"

Tester: "My Microsoft Outlook keeps freezing and crashing whenever I start my computer."

Arif:   "Umm, I understand how frustrating that is. Let's troubleshoot that together right now. Let's try starting Outlook in Safe Mode. Please press Windows key plus R, type 'outlook.exe /safe', and press Enter. Did that resolve the issue for you?"

Tester: "Yes, it opened immediately without freezing! Thank you."

Arif:   "Excellent! I'm glad we could get that resolved for you today. I have logged and closed this ticket under reference number H D 2 0 2 6 0 0 1 2. Let me repeat that for your records: H D 2 0 2 6 0 0 1 2. Is there anything else I can help you with today?"

Tester: "No, that was all. Goodbye."

Arif:   "Thank you for calling National Finance IT Support. Goodbye."
[Line Automatically Disconnects via close_call and AMI Hangup]
```

#### Verification & Expected Outputs:
1. **Telephony / Audio:** Full-duplex conversational audio with zero dead air; ticket recited slowly digit-by-digit (`H - D - 2 - 0 - 2 - 6 - 0 - 0 - 1 - 2`) and repeated for confirmation.
2. **Automated Call Hangup:** As soon as the tester says "No, that was all", Arif executes `close_call`, speaks the brief farewell, and Asterisk AMI `hangup_channel` immediately drops the call, releasing the channel with zero token waste.
3. **Frappe Helpdesk (`http://<vm2-ip>:8000`):**
   - New ticket created: `HD-2026-0012`
   - Status: `Resolved`
   - Subject: `Resolved: Outlook crash / freezing during startup`
   - Custom Field `custom_fcr`: `1` (First Contact Resolution achieved)
4. **Executive Dashboard (`https://<vm1-ip>:8090`):**
   - `Total Calls` counter increments by 1.
   - `AI Deflected Calls` counter increments by 1.
   - `FCR Deflection Rate` chart updates dynamically.
   - Transcript bubbles visible in the Call Inspection Drawer with all turns logged.

---

### Scenario 2: Unresolved Diagnostic Escalation & Agent Queue Transfer (Arabic)
*Tests Arabic speech recognition, compound number normalization, unresolved diagnostic outcome detection, pre-flight queue availability check, and AMI redirection to Queue 7001.*

- **Caller Persona:** Haitham Al Sabahi (ID: `1003`, Phone: `+96890000003`, Standard)
- **Target Issue:** Wi-Fi authentication failure / corporate network drop

#### Turn-by-Turn Dialogue:
```
[الاتصال يرن ويجيب عارف]
عارف:   "Welcome to National Finance IT Support. For English please say English. للغة العربية قل عربي."

المختبر: "العربية لو سمحت."

عارف:   "شكراً لك. تفضل بالاسم الكامل لو سمحت؟"

المختبر: "هيثم الصباحي."

عارف:   "أهلاً بك يا أخ هيثم. يرجى تزويدي بالرقم الوظيفي الخاص بك المكون من أربعة أرقام؟"

المختبر: "ألف وثلاثة." (Compound Arabic: 1003)

عارف:   "تم التحقق من بياناتك بنجاح. كيف أقدر أساعدك اليوم في الدعم الفني؟"

المختبر: "شبكة الواي فاي للشركة تفصل باستمرار ولا تقبل الاتصال."

عارف:   "تمام، فهمت عليك تماماً، ولا تشيل هم بنحل المشكلة معك خطوة بخطوة. كخطوة أولى: تأكد أنك متصل بشبكة NF-Corporate وليست شبكة الزوار، وتأكد من زر تفعيل الواي فاي. هل تم حل المشكلة معك الآن؟"

المختبر: "جربت ومزالت المشكلة مستمرة."

عارف:   "تمام، وصلت الفكرة. نجرب الخطوة الثانية: قم بحذف شبكة NF-Corporate عبر Forget Network ثم أعد إدخال بياناتك الوظيفية. هل اشتغلت معك الحين؟"

المختبر: "ما زالت تفصل وترفض كلمة المرور."

عارف:   "وصلت الفكرة. نجرب الخطوة الثالثة: افتح موجه الأوامر cmd واكتب الأمر ipconfig /renew ثم اضغط Enter. جرب معي الحين وقولي وش يطلع معك؟"

المختبر: "سويت الخطوة وما زالت نفس المشكلة."

عارف:   "لقد قمنا بتجربة الخطوات التشخيصية السابقة ولم تُحل المشكلة بعد. هل تود أن أنشئ لك تذكرة دعم فني الآن ليتابع معك مهندس الدعم الفني مباشرة؟"

المختبر: "نعم، افتح لي تذكرة لو سمحت."

عارف:   "تم تسجيل تذكرتك بنجاح برقم مرجعي: H D 2 0 2 6 0 0 1 3. وأكرر الرقم للتأكيد: H D 2 0 2 6 0 0 1 3. سيتابع معك مهندس الدعم الفني مباشرة لحل المشكلة. هل هناك أي استفسار آخر يمكنني مساعدتك به؟"

المختبر: "لا شكراً، مع السلامة."

عارف:   "شكراً لاتصالك بدعم تقنية المعلومات في ناشيونال فاينانس. مع السلامة."
[يتم إنهاء المكالمة وإغلاق الخط تلقائياً]
```

#### Verification & Expected Outputs:
1. **Compound Digit Normalization:** Spoken Arabic `"ألف وثلاثة"` correctly normalized to integer `1003`.
2. **Pre-Flight Queue Check:** `check_queue_availability('standard', '7001')` executes in background. Verifies human agent is logged in before redirecting.
3. **AMI Channel Variables:** Asterisk sets:
   - `AI_CALLER_NAME="Haitham Al Sabahi"`
   - `AI_EMPLOYEE_ID="1003"`
   - `AI_TICKET_NUMBER="HD-2026-0013"`
   - `AI_REASON="Wi-Fi authentication failure"`
4. **Frappe Helpdesk:**
   - Ticket `HD-2026-0013` created with status `Open`, assigned to `Service Desk` group.

---

### Scenario 3: Human Agent Unavailable Graceful Recovery -> Scheduled Callback
*Tests graceful failure handling when Queue 7001 is empty or outside working hours; verifies Arif offers scheduled callback rather than dropping the call into endless hold music.*

- **Pre-Test Condition:** Log out all human agents from Queue `7001` or set `ASTERISK_QUEUE_STANDARD=9999` in `.env`.
- **Caller Persona:** Mohammed Akheel (ID: `1002`)
- **Target Issue:** VPN Cisco AnyConnect login failure

#### Turn-by-Turn Dialogue:
```
[After Arif attempts agent transfer]
Arif:   "I apologize, all our IT support specialists are currently assisting other callers and unavailable in the queue. Your reference ticket number is H D 2 0 2 6 0 0 1 4. Would you like me to schedule a callback so an engineer can reach out to you directly?"

Tester: "Yes please, have them call me back on my mobile in the afternoon."

Arif:   "Certainly. What is the best contact phone number to reach you?"

Tester: "Nine zero zero zero zero zero zero one."

Arif:   "Your callback request has been registered under ticket H D 2 0 2 6 0 0 1 4. An IT support specialist will call you back at 90000001 during the afternoon. Is there anything else I can help you with?"

Tester: "No, thank you Arif."

Arif:   "Thank you for calling National Finance IT Support. Goodbye."
```

#### Verification & Expected Outputs:
1. **Telephony Behavior:** Dead air completely eliminated. Zero abrupt hangup.
2. **Frappe Helpdesk:**
   - Ticket `HD-2026-0014` updated with `subject: Callback Request - VPN Cisco AnyConnect login failure`.
   - Description notes: `Preferred Time: afternoon | Callback Phone: 90000001`.
   - Priority escalated to `High`.

---

### Scenario 4: C-Suite Executive Automatic Fast-Track (P0_EXECUTIVE)
*Tests automatic Caller-ID (CLI) matching for CEO / CFO. System bypasses standard diagnostic questions and immediately transfers to Executive Concierge Desk (Queue 7002).*

- **Caller Persona:** Khalid Al Harthy (CEO, ID: `1000`, Phone: `+96899000001`, Tier: `P0_EXECUTIVE`)
- **Pre-Call Telephony Setup:** Set Softphone / Asterisk CLI caller ID to `+96899000001`.

#### Turn-by-Turn Dialogue:
```
[Asterisk Channel opens - CLI: +96899000001 detected]
Arif:   "Hi, I am Arif from National Finance IT Support team. Please say Arabic or English to continue."

Tester: "Arabic." (أو الإنجليزية)

عارف:   "أهلاً وسهلاً بك سعادة الفاضل خالد الحارثي. يسعدنا دائماً خدمتك. أحولك مباشرة إلى مكتب الدعم التنفيذي الخاص بكبار الشخصيات الآن."
[Asterisk PBX AMI instantly redirects call to Executive Queue 7002 / Cisco Webex 8002]
[Zero diagnostic questions asked - instant VIP concierge response]
```

#### Verification & Expected Outputs:
1. **Log Output (`app/openai_realtime_bridge.py`):**
   ```
   [EXECUTIVE DETECTED] CLI match for Khalid Al Harthy (CEO)
   [TRANSFER] Redirecting channel SIP/trunk-000012 to queue 7002 (Executive Concierge)
   ```
2. **Executive Dashboard (`:8090`):**
   - Call flagged with Purple Crown Badge: `P0_EXECUTIVE`.
   - `VIP Calls` KPI counter increments by 1.
   - Call status logged as `transferred` directly to `it-vip-exec`.

---

### Scenario 5: Director Priority-Assisted AI Service (P1_VIP)
*Tests priority handling for Directors / Department Heads. Diagnostic assistance is provided with priority queuing and dedicated VIP escalation paths.*

- **Caller Persona:** Ahmed Al Balushi (IT Director, ID: `1001`, Phone: `+96890000001`, Tier: `P1_VIP`)
- **Target Issue:** ERPNext database connection timeout

#### Turn-by-Turn Dialogue:
```
Tester: "English please."
Arif:   "English confirmed. May I please have your full name?"
Tester: "Ahmed Al Balushi."
Arif:   "Thank you, Ahmed. Could you please provide your employee ID?"
Tester: "One zero zero one."

Arif:   "Thank you Mr. Ahmed Al Balushi. You are verified and your profile is flagged for Priority VIP Support. How can I assist you today?"

Tester: "I am getting database connection timeouts on the financial reporting module."

Arif:   "Understood, Mr. Ahmed. As this impacts critical financial systems, I can initiate a direct priority transfer to our Senior Infrastructure Specialist, or open an urgent P1 ticket. Which do you prefer?"

Tester: "Please open an urgent ticket and transfer me."

Arif:   "I have opened Priority Ticket H D 2 0 2 6 0 0 1 5 with urgent priority. Transferring you immediately to the Senior Queue."
[Redirection to Queue 7002 executed]
```

#### Verification & Expected Outputs:
1. **Frappe Helpdesk:**
   - Ticket `HD-2026-0015` created with `priority: Urgent` and SLA target response time `15 mins`.
   - Custom Field `vip`: `1`.

---

### Scenario 6: In-Band Telephone Dialpad (DTMF) Fallback
*Tests numeric keypad entry when speech recognition is impaired by loud ambient branch noise or mobile car hands-free speakerphones.*

- **Caller Persona:** Mohammed Akheel (ID: `1002`)
- **Test Execution:** Tester speaks in heavy background noise or mumbles, then enters employee ID using telephone keypad.

#### Turn-by-Turn Dialogue:
```
Arif:   "Please state your 4-digit employee ID?"

Tester: [Tester intentionally remains silent or generates static, then presses DTMF keys: 1 - 0 - 0 - 2 - #]

Arif:   "Keypad input received: one zero zero two. Verification successful, Mohammed Akheel. How can I help you today?"

Tester: "My password expired and I need to reset my Windows domain login."
```

#### Verification & Expected Outputs:
1. **In-Band WebSocket Frame:** Asterisk / softphone transmits RFC 4733 DTMF tone frames.
2. **Bridge Log:**
   ```
   [DTMF KEYPAD] Received tone: 1
   [DTMF KEYPAD] Received tone: 0
   [DTMF KEYPAD] Received tone: 0
   [DTMF KEYPAD] Received tone: 2
   [DTMF KEYPAD] Terminator '#' detected. Auto-submitting buffer: 1002
   [VERIFY] Database match: Mohammed Akheel (1002)
   ```
3. Voice assistant transitions seamlessly to technical diagnosis.

---

### Scenario 7: Spoken Gulf & Omani Arabic Compound Numerals
*Tests advanced compound numeral parsing for employee IDs spoken colloquially in Gulf/Omani dialects.*

- **Target Numbers to Test:**
  - `1001` -> `"واحد صفر صفر واحد"` (Digit-by-digit)
  - `1002` -> `"ألف واثنين"` (Compound thousands + units)
  - `1010` -> `"ألف وعشرة"` (Compound thousands + tens)
  - `1015` -> `"ألف وخمسطعش"` (Colloquial Gulf teen: 1015)
  - `1025` -> `"ألف وخمسة وعشرين"` (Compound thousands + units + tens: 1025)

#### Turn-by-Turn Dialogue:
```
عارف:   "يرجى تزويدي بالرقم الوظيفي الخاص بك؟"

المختبر: "ألف وخمسة وعشرين" (أو ألف واثنين)

عارف:   "تم التحقق من رقمك الوظيفي بنجاح. أهلاً بك..."
```

#### Verification & Expected Outputs:
- [`app/verify.py`](file:///e:/Github/callcenter/ai-support-agent/app/verify.py) correctly decomposes Arabic prefixes (`و`), multi-word numbers, and tens into integer digits without verification failure.

---

### Scenario 8: Physical Hardware Request & Department Manager Approval Governance
*Tests strict governance guardrails. Physical equipment requests must NEVER be auto-resolved; they must generate an approval workflow ticket for the Department Manager.*

- **Caller Persona:** Haitham Al Sabahi (Accountant, Finance, ID: `1003`)
- **Target Request:** Damaged laptop charger replacement and external 27-inch monitor request

#### Turn-by-Turn Dialogue:
```
Tester: "English please."
[Verification completes for Haitham Al Sabahi, ID: 1003]

Arif:   "How can I help you today?"

Tester: "My laptop charger cable has completely frayed and stopped charging, and I also need an external monitor for accounting spreadsheets."

Arif:   "I understand you require hardware replacement. For physical IT equipment, company policy requires Department Manager authorization. I have generated Hardware Request Ticket H D 2 0 2 6 0 0 1 6. This has been routed to your department manager for formal approval. Once approved, IT procurement will issue your equipment. Your ticket number is H D 2 0 2 6 0 0 1 6. Do you require any other assistance?"

Tester: "No, that is clear. Thank you."
```

#### Verification & Expected Outputs:
1. **Frappe Helpdesk:**
   - Ticket `HD-2026-0016` created.
   - `ticket_group`: `Hardware Request`
   - `workflow_state`: `Pending Approval`
   - `custom_approval_status`: `Pending Manager Approval`
   - `custom_hardware_requested`: `1`
   - SLA Clock pauses until Department Manager approves the request.

---

### Scenario 9: Severity-1 Critical Emergency Outage Escalation (Queue 7003)
*Tests critical incident keyword triggers (ransomware, core banking down, branch network outage, fire alert).*

- **Caller Persona:** Ruqaiya Al Balushi (Service Desk Lead, ID: `1008`, IT)
- **Target Issue:** Core Loan Processing Database unreachable across all nationwide branches

#### Turn-by-Turn Dialogue:
```
[Caller Verified]
Tester: "Arif, we have a critical emergency. The core banking server and Oracle financial database are completely unreachable across all branches! All teller operations are down!"

Arif:   "This has been flagged as a critical Sev-1 incident. Generating an emergency incident ticket and transferring you immediately to the on-call emergency team."
[Asterisk PBX executes immediate AMI redirect to Emergency Queue 7003]
[On-call engineer phones ring simultaneously]
```

#### Verification & Expected Outputs:
1. **Frappe Helpdesk:**
   - Ticket `HD-2026-0017` created with `priority: Critical (Sev-1)`.
   - Escalated to `Emergency Outage Incident Queue`.
   - Automated email/SMS alert dispatched to IT On-Call Duty Roster.
2. **Executive Dashboard (`:8090`):**
   - Red Flashing Alert Pill on Active Calls: `Sev-1 Critical Emergency`.
   - `Emergency Escalations` KPI counter increments.

---

### Scenario 10: Existing Ticket Status Inquiry (`check_ticket_status`)
*Tests real-time status retrieval for previously submitted tickets, including manager approval status and resolution notes.*

- **Caller Persona:** Mohammed Akheel (ID: `1002`)
- **Query:** Status of previously submitted ticket `HD-2026-0016`

#### Turn-by-Turn Dialogue:
```
[Caller Verified]
Tester: "Can you give me an update on my ticket HD-2026-0016?"
(Spoken: "H D twenty twenty-six zero zero sixteen")

Arif:   "Checking our records for ticket H D 2 0 2 6 0 0 1 6. Ticket H D 2 0 2 6 0 0 1 6 is currently Pending Approval. Approval status is: Pending Manager Approval from your Department Head. Is there anything else I can assist you with?"

Tester: "What about ticket HD-2026-0012?"

Arif:   "Ticket H D 2 0 2 6 0 0 1 2 is currently Resolved. Resolution notes: Issue resolved by starting Outlook in Safe Mode. Is there anything else I can help you with?"
```

#### Verification & Expected Outputs:
- Dynamic ticket lookup normalizes spoken variations (`"HD 2026 0016"`, `"HD-2026-0016"`, `"twenty twenty six"`) and queries Frappe API in real time.

---

### Scenario 11: Digit-by-Digit Ticket Recitation & Repetition (`repeat_ticket_number`)
*Tests caller requesting ticket number repetition without triggering redundant ticket creation.*

#### Turn-by-Turn Dialogue:
```
[Arif has just created ticket HD-2026-0018]
Arif:   "Your ticket number is H D 2 0 2 6 0 0 1 8."

Tester: "Wait, can you repeat the ticket number please? I didn't get that."

Arif:   "Certainly. Your ticket number is H D 2 0 2 6 0 0 1 8. Repeating slowly: H, D, 2, 0, 2, 6, 0, 0, 1, 8. Is there anything else you need?"

Tester: "Got it now, thank you!"
```

#### Verification & Expected Outputs:
- `repeat_ticket_number` executes cleanly; **zero duplicate tickets** created in Frappe Helpdesk or database.

---

### Scenario 12: Non-IT Inquiry Scope Rejection & Quality Gate Rejection
*Tests strict boundary guardrails: customer loan inquiries are rejected; vague complaints (<10 chars) are gated.*

#### Sub-Scenario A: Non-IT Inquiry (Customer Banking / Loan)
```
[Caller Verified]
Tester: "I want to apply for a personal car loan and check the interest rate."

Arif:   "This phone line is strictly for internal National Finance employee IT Support. For customer personal loans, vehicle financing, or branch banking services, please contact our Customer Service Call Center at 8000-0000. Is there any internal IT issue I can assist you with?"
```
*Expected Result:* Ticket creation is **blocked**; audit log records `out_of_scope_inquiry`.

#### Sub-Scenario B: Quality Gate (Vague / Short Description)
```
[Caller Verified]
Tester: "It is broken." (or "Help me" / "Test")

Arif:   "Could you please describe the specific error message or software application you are experiencing trouble with, so I can investigate properly?"
```
*Expected Result:* Rejects single-word descriptions; requires specific technical symptoms before proceeding.

---

### Scenario 13: Deactivated / Offboarded Employee Offboarding Gate
*Tests security offboarding enforcement. Deactivated employees cannot access IT support or raise tickets.*

- **Caller Persona:** Deactivated Former Employee (ID: `9999`, Phone: `+96890000999`, `active=0`)

#### Turn-by-Turn Dialogue:
```
Tester: "English please."
Arif:   "May I please have your full name?"
Tester: "Former Clerk."
Arif:   "Please provide your employee ID?"
Tester: "Nine nine nine nine."

Arif:   "This employee account is deactivated in the company directory. Please contact Human Resources or IT Administration directly for assistance. Goodbye."
[Call Clears Automatically]
```

#### Verification & Expected Outputs:
- Offboarding enforced; no ticket generated; security event `deactivated_caller_access_attempt` logged.

---

### Scenario 14: Dynamic Knowledge Base Update & Zero-Downtime Reflection
*Tests adding a new troubleshooting playbook from the Executive Dashboard and verifying that telephony calls discover the new solution within 15 seconds without restarting the service.*

#### Step 1: Add New Playbook in Dashboard (`:8090`)
1. Log in to dashboard as Admin (`https://<vm1-ip>:8090/knowledge`).
2. Click **"Add Playbook"**:
   - **Playbook Slug:** `pulse_secure_vpn`
   - **Title:** `Pulse Secure Corporate VPN Connection Fix`
   - **Category:** `Network & Connectivity`
   - **English Keywords:** `pulse secure, vpn error 1205, vpn gateway timeout`
   - **Arabic Keywords:** `شبكة وهمية, في بي ان بولس, بوابة الاتصال`
   - **Content:**
     ```markdown
     # Pulse Secure VPN Fix
     1. Right click Pulse Secure icon in taskbar tray and click Disconnect.
     2. Press Windows+R, type cmd, and run ipconfig /flushdns.
     3. Reconnect to vpn.nationalfinance.om using your domain credentials.
     ```
3. Click **"Save Playbook"**.

#### Step 2: Make Test Call Immediately (Within 15 Seconds)
```
[Caller Verified]
Tester: "My Pulse Secure VPN is giving gateway timeout error 1205."

Arif:   "Based on our troubleshooting guide for Pulse Secure VPN: please right-click the Pulse Secure icon in your taskbar, select Disconnect, then open Command Prompt, run 'ipconfig /flushdns', and reconnect to vpn.nationalfinance.om. Did that resolve the issue for you?"
```

#### Verification & Expected Outputs:
- New article loaded dynamically via `_KB_CACHE` (15s TTL). **Zero downtime, zero service restarts.**

---

### Scenario 15: Mandatory 3 to 4 Diagnostic Steps, Ticket Consent & IT Engineer Follow-Up Recital
*Tests strict sequential 3-4 steps troubleshooting, zero hesitation on Step 2, caller consent check prior to ticket creation, and mandatory IT support engineer follow-up announcement with ticket repetition.*

- **Caller Persona:** Ruqaiya Al Balushi (ID: `1008`, Phone: `+96890000008`, VIP Tier: `P1_VIP`)
- **Target Issue:** Microsoft Teams microphone and audio device failure

#### Turn-by-Turn Dialogue:
```
[PBX Connects Call]
Arif:   "Welcome to National Finance IT Support. For English please say English. للغة العربية قل عربي."

Tester: "English."

Arif:   "Thank you for choosing English. May I please have your full name?"

Tester: "Ruqaiya Al Balushi."

Arif:   "Could you please provide your 4-digit employee ID?"

Tester: "One zero zero eight."

Arif:   "Thank you Ruqaiya Al Balushi, you are verified for priority support. How can I assist you today?"

Tester: "My microphone in Microsoft Teams is not working during calls."

Arif:   "Umm, I understand how frustrating that is. Let's troubleshoot that together right now. Step 1: In Microsoft Teams, click Settings, go to Devices, and make sure your corporate headset is selected as the primary Audio device rather than PC Mic. Did that resolve the issue for you?"

Tester: "I checked it, headset is selected but still no sound."

Arif:   "Got it, thanks for checking that. Let's move to Step 2: Open Windows Sound Settings, verify your microphone privacy permissions are turned on for Microsoft Teams, and test the microphone input level. Could you test that now?"

Tester: "Permissions are on, but when I speak the bar does not move."

Arif:   "Understood. Let's try Step 3: Unplug your headset USB cable, plug it into a different USB port on your laptop, and toggle Windows audio device off and back on. Did that resolve the issue for you?"

Tester: "No, tried a different port and it is still not picking up my voice."

Arif:   "Those 3-4 diagnostic steps did not resolve the issue. Shall I create a support ticket for you now so our IT support engineer can follow up with you directly?"

Tester: "Yes please, create the ticket."

Arif:   "Your IT ticket has been logged under reference number H D 2 0 2 6 0 0 1 5. Let me repeat that for your records: H D 2 0 2 6 0 0 1 5. Our IT support engineer will follow up with you directly. Is there anything else I can assist you with today?"

Tester: "That is all, thank you."

Arif:   "Thank you for calling National Finance IT Support. Goodbye."
[Line Automatically Disconnects via close_call and AMI Hangup]
```

#### Verification & Expected Outputs:
1. **Zero Turn Latency:** Immediate transition from Step 1 to Step 2 without awkward 10-17s dead air.
2. **Explicit Consent Gate:** AI did not force ticket creation without asking; explicitly requested caller confirmation after 3-4 steps.
3. **Support Engineer Recital:** Explicitly stated that an **IT support engineer** will follow up directly, recited ticket number digit-by-digit, and repeated it once for clarity.
4. **Automated Line Disconnect:** Line drops cleanly when tester says "That is all, thank you".

---

### Scenario 16: Angry / Frustrated Caller De-escalation Protocol (Corporate Empathy & Expedited Escalation)
*Tests tone awareness, polite corporate de-escalation, sincere apology, and urgent priority escalation for frustrated callers.*

- **Caller Persona:** Mohammed Akheel (ID: `1002`, Phone: `+919000000001`, Standard)
- **Target Issue:** VPN repeatedly disconnecting during urgent executive presentation (Caller is agitated and speaking in an upset tone)

#### Turn-by-Turn Dialogue:
```
[Caller Verified]
Tester: (Angry tone) "I am extremely furious! My VPN keeps disconnecting every 5 minutes and I have a critical presentation with the board in 10 minutes! This is totally unacceptable!"

Arif:   "I completely understand how frustrating this issue is for you, and I sincerely apologize for the inconvenience. Let me take personal ownership of this right now to get it resolved for you as quickly as possible. Would you like me to guide you through a quick 30-second network reset, or should I immediately log an urgent high-priority ticket for our desktop engineering team to call you right now?"

Tester: (Impatient) "No more troubleshooting! Get an engineer on my phone right away!"

Arif:   "I understand your frustration completely. I have logged an urgent high-priority ticket for you under reference number H D 2 0 2 6 0 0 1 6. Let me repeat that for your records: H D 2 0 2 6 0 0 1 6. Our senior desktop engineering team will contact you immediately as a top priority. Is there anything else I can assist you with right now?"

Tester: "No, that's it."

Arif:   "Thank you for calling National Finance IT Support. Goodbye."
[Line Automatically Disconnects via close_call and AMI Hangup]
```

#### Verification & Expected Outputs:
1. **Demeanor & Empathy:** Zero defensiveness; sincere apology acknowledging urgency and frustration.
2. **Priority Escalation:** Ticket created in Frappe Helpdesk with `priority: 3 high` and flagged with customer urgency notes.
3. **Immediate Disconnect:** Call releases smoothly when tester indicates they are done.

---

### Scenario 17: Conversational Interruption & Barge-In without Awkward Fillers
*Tests full-duplex server VAD barge-in responsiveness and verifies complete elimination of awkward conversational fillers like "Take your time" or "Whenever you're ready".*

- **Caller Persona:** Haitham Al Sabahi (ID: `1003`, Phone: `+96890000003`, Standard)
- **Target Issue:** Password expiry and domain login lock

#### Turn-by-Turn Dialogue:
```
[Caller Verified]
Arif:   "Let's troubleshoot your login issue. First, please ensure your Caps Lock key is turned off, and verify that..."

Tester: [Interrupts mid-sentence] "Wait, I already checked Caps Lock and rebooted twice! It explicitly says 'Your domain password has expired'."

Arif:   [Instantly stops speaking on interruption, zero filler phrase]
Arif:   "Understood. Since your domain password has expired, you can reset it self-service by pressing Ctrl+Alt+Delete and selecting 'Change a password', or using the National Finance self-service portal at sspr.nationalfinance.om. Did that resolve the issue for you?"
```

#### Verification & Expected Outputs:
1. **Audio Socket Pacing:** Audio immediately halts on caller speech detection with `clear_outbound_queue()`.
2. **Anti-Filler Guardrail:** Arif does **NOT** say *"Take your time"*, *"Whenever you're ready"*, or *"Sure thing"*. Directly addresses the expired password fact without delay.

---

### Scenario 18: Telephony Call Disconnect on Wrap-Up / Declining Further Assistance
*Tests mandatory call teardown when caller declines further help, verifying zero token waste and zero idle channel hold.*

- **Caller Persona:** Mohammed Akheel (ID: `1002`)
- **Target Issue:** General IT inquiry or resolved ticket

#### Turn-by-Turn Dialogue:
```
Arif:   "...Is there anything else I can help you with today?"

Tester: "No, that's all. Thank you." (or "Nothing else", "I'm good", "لا شكراً مع السلامة")

Arif:   "Thank you for calling National Finance IT Support. Goodbye."
[PBX AudioSocket sends 0x00 Hangup frame and AMI executes Action: Hangup on SIP channel]
```

#### Verification & Expected Outputs:
1. **Channel Status:** Asterisk CLI confirms `Channel SIP/... has been hung up`.
2. **Dashboard Status:** Active Calls table updates to 0 within seconds.
3. **Billing & Tokens:** Realtime WebSocket closes immediately, halting OpenAI token consumption.

---

## Post-Test Telemetry & Supervisory Verification Runbook

After completing any test call, the test supervisor should verify telemetry across the following three interfaces:

### 1. Executive Dashboard Live Inspection (`https://<vm1-ip>:8090/calls`)
- Locate the call record by `Call ID` or `Caller Number`.
- Click the **Inspect Drawer** icon:
  - Verify **Audio Playback**: Full WAV recording should play back cleanly with both channels audible.
  - Verify **Transcript Timeline**: Colored speech bubbles with exact caller phrases and Arif's spoken responses.
  - Verify **KPI Badges**: `AI Deflected`, `Human Transferred`, or `Emergency Escalated`.

### 2. Frappe Helpdesk Admin Verification (`http://<vm2-ip>:8000/app/hd-ticket`)
- Filter tickets created within the last hour.
- Verify ticket fields:
  - `custom_call_id` matches the dashboard call ID.
  - `custom_caller_phone` matches caller CLI.
  - `workflow_state` reflects resolution (`Resolved`) or transfer state (`Open` / `Pending Approval`).

### 3. Tamper-Evident SHA-256 Audit Ledger (`https://<vm1-ip>:8090/security-events`)
- Inspect cryptographic audit entries.
- Verify `record_hash` and `prev_hash` chain continuity:
  ```
  Verified: SHA-256 ledger integrity 100% untampered.
  ```

---

## Test Failure & Troubleshooting Checklist

| Symptom | Probable Root Cause | Resolution Action |
|---|---|---|
| **Arif does not speak initial greeting** | Asterisk WebSocket audio codec mismatch or missing speech payload | Ensure Asterisk endpoint has `allow=ulaw,alaw` and WebSocket connects to `127.0.0.1:8765`. |
| **Arif interrupts caller while speaking** | VAD silence threshold too aggressive | Adjust `VAD_THRESHOLD=0.55` and `VAD_SILENCE_MS=600` in `.env`. |
| **Caller verification fails on spoken Arabic numbers** | Heavy colloquial compound numbers | Spoken words normalized in `app/verify.py`. Check that Arabic aliases exist in `/callers` table. |
| **Transfer to Queue 7001 results in immediate callback offer** | 0 human agents available in queue | Expected behavior (Scenario 3). Log an Asterisk agent into queue `7001` via `*54` to test live bridge. |
| **Hardware ticket created with Resolved status** | Policy violation in custom prompt | Check `SYSTEM_PROMPT` in `prompts.html`; hardware MUST route to `create_ticket(group="Hardware Request")`. |

---
*End of Call Testing Scenarios Guide. Maintained by National Finance IT Support Architecture Team.*
