Here is the complete implementation plan for the 3 Caller-ID (CLI) options, followed by the step-by-step server deployment guide for the Phases 1–3 audio resilience updates.

Part 1: Implementation Plan for the 3 CLI Options
To avoid hardcoding one behavior and give you full enterprise flexibility, we can implement a clean configuration setting: CLI_FAST_TRACK_MODE in 

app/config.py
 and 

.env
.

ini
# CLI Pre-Identification Policy: "disabled" | "whitelist_only" | "auto_transfer"
CLI_FAST_TRACK_MODE="disabled"
CLI_EXECUTIVE_WHITELIST="1000,1010"  # Specific IDs permitted if mode is whitelist_only
Comparison of the 3 Options
Inbound SIP Call Arrives (CLI / Extension detected)
                      │
                      ▼
         ┌─────────────────────────┐
         │  CLI_FAST_TRACK_MODE?   │
         └────────────┬────────────┘
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
  [Option A]     [Option B]     [Option C]
  "disabled"  "whitelist_only" "auto_transfer"
       │              │              │
       │              │              ├─► Is caller on VIP whitelist?
       │              │              │     YES: Bypass AI completely.
       │              │              │     Execute Asterisk AMI Redirect
       │              │              │     directly to Webex Queue 920.
       │              │              │
       │              ├─► Check ID against
       │              │   explicit whitelist (1000, 1010):
       │              │     MATCH: Proceed with Executive flow.
       │              │     NO MATCH: Demote to standard caller.
       │              │
       ▼              ▼
  Treat as Unverified Caller:
  1. Greet neutrally ("Welcome to National Finance IT Support...")
  2. Caller chooses language.
  3. AI asks: "May I please have your full name?"
  4. AI asks for Employee ID.
  5. Only verified after explicit validation.
Detailed Plan for Each Option
Option A: Strict Verification for All Callers (CLI_FAST_TRACK_MODE="disabled")
Behavior:
The agent never pre-populates state["caller_name"] or marks state["is_executive"] = True based on caller ID.
The incoming phone number is still saved for call telemetry, rate limiting, and security logs, but the identity gate remains closed.
Every caller—regardless of whether they are an accountant, manager, or executive—must state their name and recite their employee ID before any tickets or transfers are initiated.
Code Changes:
In 

app/openai_realtime_bridge.py
, wrap the pre-identification block with if CLI_FAST_TRACK_MODE != "disabled":.
If disabled, leave state["caller_name"] = None and state["verified_user"] = None.
Pros: Eliminates the "how did you know my name?" shock; 100% compliant with zero-trust security.
Option B: Strict CEO & CFO Whitelist (CLI_FAST_TRACK_MODE="whitelist_only")
Behavior:
Regular AD accounts (even those in "Executive" Active Directory OUs like department managers or accountants) will not trigger fast-track identification.
Only employee IDs explicitly listed in CLI_EXECUTIVE_WHITELIST="1000,1010" (CEO and CFO) are pre-identified.
If your number is not explicitly in that list, you are treated as an unverified caller.
Code Changes:
In 

app/openai_realtime_bridge.py
, evaluate:
python
if pre_user and str(pre_user.get("employee_id")) in CLI_EXECUTIVE_WHITELIST:
    # Only allow pre-identification for verified whitelist
Prevent broader Active Directory security groups from accidentally elevating regular users.
Pros: Preserves the white-glove executive VIP experience for the CEO/CFO without false positives for other staff.
Option C: Deterministic Immediate Transfer (CLI_FAST_TRACK_MODE="auto_transfer")
Behavior:
When an authorized executive calls from their known phone, the bridge bypasses the OpenAI conversational engine entirely.
Instead of asking OpenAI to say "Welcome Mr. X, I will transfer you..." (which risks the LLM hallucinating "Hello, how can I help you"), the Python bridge executes the Asterisk AMI redirect directly to the Executive queue (920 / 8002) immediately upon connection.
Code Changes:
In 

app/openai_realtime_bridge.py
, trigger transfer_call(channel, extension="920") via AMI before opening the OpenAI Realtime audio stream.
Pros: Zero latency, 0% chance of conversational hallucination or unexpected greeting words.