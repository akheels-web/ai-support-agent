import asyncio
import base64
import json
import os
import websockets
from dotenv import load_dotenv

load_dotenv("/opt/ai-support-agent/.env")

OPENAI_API_KEY        = os.getenv("OPENAI_API_KEY")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
ASTERISK_WS_HOST      = "127.0.0.1"
ASTERISK_WS_PORT      = 8765
OPENAI_WS_URL         = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

# ---------------------------------------------------------------------------
# System prompt — compact but complete
# Keep instructions as terse as possible to reduce every-turn token cost.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """
You are Arif, AI IT Support voice agent for National Finance IT Support.

=== CALL FLOW (follow in order) ===

1. GREETING
   Say exactly once: "Hi, I am Arif from National Finance IT Support. Please say Arabic or English to continue."

2. LANGUAGE
   Wait for "Arabic" or "English". Confirm and stay in that language for the whole call. Never mix.

3. VERIFICATION — MANDATORY before any ticket or advice
   Ask for: (a) full name, (b) employee ID.
   Call verify_user with BOTH fields. Only proceed if it returns verified=true.
   If verification fails:
     - Tell the caller politely that details did not match and ask them to try again.
     - Track internally: after 3 consecutive failures call close_call with reason="verification_failed".
       Say: "I'm sorry, I cannot verify your details. Please get the correct information and call back. Thank you."
   Do NOT proceed with any support or ticket if the user is not verified.

4. ISSUE COLLECTION
   Ask: "How can I help you today?" Listen carefully. Ask at most ONE clarifying question if needed.

5. TROUBLESHOOTING (on-call resolution — try this first)
   Use the knowledge base below. Walk through up to 3-4 steps maximum.
   After each step ask: "Has that resolved the issue?"
   If caller says yes → go to step 6 (wrap-up).
   If still unresolved after 3-4 steps → go to step 7 (ticket).

6. WRAP-UP (issue resolved without ticket)
   Say: "Great, glad that's sorted! Is there anything else I can help you with?"
   If nothing else → say goodbye and call close_call with reason="resolved".

7. TICKET CREATION
   Say: "Let me raise a ticket for you now." Then immediately call create_ticket — do not narrate further until you have the result.
   On success: read ticket number clearly. Ask if anything else is needed.
   On error: say "I'm having trouble creating the ticket right now. Please email itsupport@nationalfinance.com with your employee ID and issue."
   After ticket is confirmed and caller is done → call close_call with reason="ticket_created".

=== STRICT TOOL RULES ===
- Never say "I've created a ticket" without first calling create_ticket and receiving success=true.
- Never invent ticket numbers.
- Always call close_call to end the call — never just say goodbye and go silent.
- If a tool is running, say "Please hold one moment" once and wait for the result.
- Do NOT repeat yourself or overlap speech. Wait for tool results before continuing.

=== INTERRUPTION HANDLING ===
- If caller speaks while you are mid-sentence, stop and listen. Prioritise what they said.
- For short filler words ("yes", "ok", "hmm") — do not restart your response from scratch, just continue.

=== IT KNOWLEDGE BASE ===

** Password / Account lockout **
- Active Directory lockout: ask which system (Windows/VPN/email).
- Standard fix: IT team must unlock via AD — you cannot do this remotely. Raise a ticket (Service Desk, high priority if it blocks work).
- Self-service (if portal available): direct to https://selfservice.nationalfinance.com/reset
- Never suggest "just restart" for lockouts — it does not help.

** MFA / Authenticator issues **
- App not generating codes: check phone time sync (Settings → Date & Time → Automatic).
- Lost/new phone: IT must re-provision MFA. Raise ticket (Security team, high priority).
- Backup codes: if caller has backup codes, walk them through using one.

** VPN issues **
- Ask: What error do you see? (TLS error / timeout / "no connection" / credential error)
- TLS error before login: certificate issue → raise ticket (Network Support).
- TLS error after login: expired user cert → raise ticket (Network Support, high priority).
- Timeout / no connection: (1) Check internet works. (2) Try alternate VPN server if available. (3) Restart VPN client. (4) If still failing → ticket (Network Support).
- Credential error: verify credentials are correct, then check lockout status → ticket if locked.
- Cisco AnyConnect: logs at Help → Diagnostics → View Logs. Ask caller to note last error line.
- GlobalProtect: check status icon colour. Yellow = partial, Red = disconnected. Right-click → Connect.

** Slow PC / performance **
- Quick wins: (1) Restart (if not done in >3 days). (2) Close unused browser tabs. (3) Check Task Manager for high-CPU processes (Ctrl+Shift+Esc).
- Persistent issue after restart: raise ticket (Service Desk) — hardware or AV scan needed.

** Email (Outlook) issues **
- Not receiving mail: check Junk and Clutter folders. Check inbox rules (Home → Rules).
- Outlook offline: click Send/Receive → Work Offline toggle.
- Calendar not syncing: File → Account Settings → repair Exchange account.
- Profile corruption: raise ticket — do not ask caller to delete and recreate profile themselves.

** Printer issues **
- Not printing: (1) Check printer is online (blue light). (2) Clear print queue (Services → Print Spooler → restart). (3) Reinstall driver → ticket if needed.
- Wrong default printer: Settings → Devices → Printers → set default.

** Software / application errors **
- Error codes: ask caller to note the full error message/code.
- Crash on open: (1) Run as administrator. (2) Clear temp files (%temp%). (3) If licensed software — raise ticket for reinstall.
- Access denied: raise ticket — permissions change needed (Application Support).

** Network / internet (office) **
- No internet: check if others affected (wider outage). If isolated → (1) ipconfig /release then /renew. (2) Restart NIC.
- Can't reach internal servers: check VPN status first. Ping gateway.

** New equipment / access requests **
- Always raise a ticket — IT cannot provision without formal approval trail.

** General escalation rule **
- Account-related, security, MFA, and access issues: always create a ticket. Never attempt to resolve on call beyond initial diagnosis.
- If unsure: raise a ticket rather than guess.

=== RESPONSE STYLE ===
- Short, phone-friendly sentences. No bullet lists aloud.
- One instruction or question at a time.
- Speak ticket numbers digit-by-digit (e.g. "one two three four five").
- In Arabic, use formal Gulf Arabic for professionalism.
- Never read out URLs unless caller explicitly asks.
"""

# ---------------------------------------------------------------------------
# Tool definitions — kept minimal to reduce prompt token overhead
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "name": "verify_user",
        "description": "Verify caller identity by matching employee name AND employee ID against records.",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id":   {"type": "string", "description": "Employee ID provided by caller"},
                "employee_name": {"type": "string", "description": "Full name provided by caller"}
            },
            "required": ["employee_id", "employee_name"]
        }
    },
    {
        "type": "function",
        "name": "create_ticket",
        "description": "Create a support ticket in Zammad. Only call after caller is verified.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_email": {"type": "string"},
                "title":          {"type": "string"},
                "description":    {"type": "string"},
                "priority":       {"type": "string", "enum": ["1 low", "2 normal", "3 high"]},
                "group":          {"type": "string", "description": "Team: Service Desk / Network Support / Security / Application Support"}
            },
            "required": ["customer_email", "title", "description", "priority", "group"]
        }
    },
    {
        "type": "function",
        "name": "close_call",
        "description": "Terminate the call cleanly. Always call this to end the call — never just go silent.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": ["resolved", "ticket_created", "verification_failed", "caller_requested", "timeout"],
                    "description": "Why the call is ending"
                }
            },
            "required": ["reason"]
        }
    }
]


# ---------------------------------------------------------------------------
# Session config — key fixes:
#   output_modalities = ["text","audio"]  → allows function_call items to flow
#   interrupt_response = False            → prevents AI being cut off mid-tool
#   idle_timeout_ms reduced              → faster silence detection
# ---------------------------------------------------------------------------
def build_session_config() -> dict:
    return {
        "type": "session.update",
        "session": {
            "instructions": SYSTEM_PROMPT,
            "modalities": ["text", "audio"],          # FIX: text required for tool calls
            "tools": TOOLS,
            "tool_choice": "auto",
            "input_audio_format":  "g711_ulaw",       # pcmu = g711_ulaw in Realtime API
            "output_audio_format": "g711_ulaw",
            "voice": "alloy",
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.50,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 600,           # slightly longer = fewer false cuts
                "create_response": True,
                "interrupt_response": False,           # FIX: don't cut AI during tool calls
            },
            "temperature": 0.6,                       # lower = more predictable, fewer hallucinated ticket numbers
            "max_response_output_tokens": 400,        # phone turns are short — saves tokens
        }
    }


async def connect_openai() -> websockets.WebSocketClientProtocol:
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}
    ws = await websockets.connect(OPENAI_WS_URL, additional_headers=headers, max_size=None)

    await ws.send(json.dumps(build_session_config()))

    # Trigger greeting immediately — no need for a separate response.create;
    # the system prompt instructs the model to start with the greeting.
    await ws.send(json.dumps({
        "type": "response.create",
        "response": {
            "modalities": ["text", "audio"],
            "instructions": (
                "Start the call now. Say exactly: "
                "'Hi, I am Arif from National Finance IT Support. "
                "Please say Arabic or English to continue.' "
                "Nothing else."
            ),
            "max_output_tokens": 80,
        }
    }))
    return ws


# ---------------------------------------------------------------------------
# Per-call handler
# ---------------------------------------------------------------------------
async def handle_asterisk_call(asterisk_ws: websockets.WebSocketServerProtocol, path: str):
    # ── call state ──────────────────────────────────────────────────────────
    state = {
        "verified_user":          None,
        "verification_attempts":  0,
        "last_ticket_number":     None,
        "call_ending":            False,   # set when close_call fires
        "tool_in_progress":       False,   # suppress VAD interrupts during tool
    }

    print("[ASTERISK] New call connected")

    try:
        openai_ws = await connect_openai()
        print("[OPENAI] Connected")
    except Exception as exc:
        print(f"[OPENAI] Connection failed: {exc!r}")
        await asterisk_ws.close()
        return

    # ── tool handler ────────────────────────────────────────────────────────
    async def handle_tool_call(event: dict):
        item      = event.get("item", {})
        tool_name = item.get("name", "")
        call_id   = item.get("call_id", "")
        try:
            arguments = json.loads(item.get("arguments", "{}"))
        except Exception:
            arguments = {}

        print(f"[TOOL] {tool_name} args={arguments}")
        state["tool_in_progress"] = True

        try:
            result = await _execute_tool(tool_name, arguments, state, asterisk_ws)
        except Exception as exc:
            print(f"[TOOL] Unhandled error: {exc!r}")
            result = {"success": False, "error": str(exc)}
        finally:
            state["tool_in_progress"] = False

        # Return result to model
        await openai_ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result)
            }
        }))

        # Only request a new response if we're not closing the call
        if not state["call_ending"]:
            await openai_ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"],
                    "max_output_tokens": 300,
                }
            }))

    # ── Asterisk → OpenAI ───────────────────────────────────────────────────
    async def asterisk_to_openai():
        try:
            async for message in asterisk_ws:
                if state["call_ending"]:
                    break
                if isinstance(message, bytes):
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(message).decode()
                    }))
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            print(f"[AST→OAI] {exc!r}")

    # ── OpenAI → Asterisk ───────────────────────────────────────────────────
    async def openai_to_asterisk():
        try:
            async for raw in openai_ws:
                if state["call_ending"]:
                    break

                event      = json.loads(raw)
                event_type = event.get("type", "")

                if event_type == "response.audio.delta":
                    audio_b64 = event.get("delta", "")
                    if audio_b64:
                        await asterisk_ws.send(base64.b64decode(audio_b64))

                elif event_type == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        await handle_tool_call(event)

                elif event_type == "response.done":
                    resp = event.get("response", {})
                    print(f"[OPENAI] Response done — status={resp.get('status')}")

                elif event_type == "error":
                    err = event.get("error", {})
                    print(f"[OPENAI ERROR] code={err.get('code')} msg={err.get('message')}")

                elif event_type in (
                    "input_audio_buffer.speech_started",
                    "input_audio_buffer.speech_stopped",
                    "session.updated",
                    "session.created",
                    "conversation.item.created",
                    "response.created",
                    "response.output_item.added",
                    "response.content_part.added",
                    "response.content_part.done",
                    "response.audio.done",
                    "response.audio_transcript.delta",
                    "response.audio_transcript.done",
                    "response.text.delta",
                    "response.text.done",
                    "rate_limits.updated",
                ):
                    pass  # expected events — no action needed

                else:
                    if event_type:
                        print(f"[OPENAI EVENT] {event_type}")

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            print(f"[OAI→AST] {exc!r}")

    await asyncio.gather(asterisk_to_openai(), openai_to_asterisk())
    print("[CALL] Handler exiting")


# ---------------------------------------------------------------------------
# Tool execution — separated for clarity
# ---------------------------------------------------------------------------
async def _execute_tool(
    tool_name: str,
    arguments: dict,
    state: dict,
    asterisk_ws: websockets.WebSocketServerProtocol,
) -> dict:

    # ── verify_user ─────────────────────────────────────────────────────────
    if tool_name == "verify_user":
        from app.verify import verify_user  # expects (employee_id, employee_name) → dict

        employee_id   = arguments.get("employee_id", "").strip()
        employee_name = arguments.get("employee_name", "").strip()

        result = verify_user(employee_id, employee_name)   # updated signature — see note below

        if result.get("verified"):
            state["verified_user"]         = result
            state["verification_attempts"] = 0
            print(f"[VERIFY] Verified: {employee_name} ({employee_id})")
            return {"verified": True, "name": result.get("name"), "email": result.get("email")}
        else:
            state["verification_attempts"] += 1
            attempts_left = 3 - state["verification_attempts"]
            print(f"[VERIFY] Failed attempt {state['verification_attempts']}/3")

            if state["verification_attempts"] >= 3:
                state["call_ending"] = True
                await asyncio.sleep(6)   # allow goodbye audio to finish
                try:
                    await asterisk_ws.close()
                except Exception:
                    pass
                return {
                    "verified": False,
                    "action":   "drop_call",
                    "message":  "Maximum verification attempts reached. End the call with the goodbye phrase."
                }

            return {
                "verified":      False,
                "attempts_left": attempts_left,
                "message":       "Name and employee ID did not match our records."
            }

    # ── create_ticket ────────────────────────────────────────────────────────
    elif tool_name == "create_ticket":
        if not state["verified_user"]:
            return {"success": False, "error": "Caller not verified. Cannot create ticket."}

        from app.zammad_api import create_ticket

        customer_email = state["verified_user"].get("email") or arguments.get("customer_email", "")
        title          = arguments.get("title", "IT Support Request")
        description    = arguments.get("description", "Issue reported via AI voice agent.")
        priority       = arguments.get("priority", "2 normal")
        group          = arguments.get("group", "Service Desk")

        result = create_ticket(
            customer_email=customer_email,
            title=title,
            body=description,
            group=group,
            priority=priority,
        )

        ticket_number = result.get("ticket_number")
        if ticket_number:
            state["last_ticket_number"] = ticket_number
            print(f"[ZAMMAD] Ticket created: {ticket_number}")
            return {
                "success":       True,
                "ticket_number": ticket_number,
                "message":       "Ticket created. Read the ticket number to the caller now."
            }
        else:
            print(f"[ZAMMAD] Ticket creation failed: {result}")
            return {"success": False, "error": result.get("error", "Unknown error from Zammad")}

    # ── close_call ───────────────────────────────────────────────────────────
    elif tool_name == "close_call":
        reason = arguments.get("reason", "unknown")
        print(f"[CALL] close_call triggered — reason={reason}")
        state["call_ending"] = True

        await asyncio.sleep(5)   # let goodbye audio stream out fully
        try:
            await asterisk_ws.close()
        except Exception:
            pass
        return {"closed": True, "reason": reason}

    # ── unknown ──────────────────────────────────────────────────────────────
    else:
        return {"success": False, "error": f"Unknown tool: {tool_name}"}


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------
async def main():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing in /opt/ai-support-agent/.env")

    print(f"[SERVER] Starting on {ASTERISK_WS_HOST}:{ASTERISK_WS_PORT}")
    print(f"[SERVER] Model: {OPENAI_REALTIME_MODEL}")

    async with websockets.serve(
        handle_asterisk_call,
        ASTERISK_WS_HOST,
        ASTERISK_WS_PORT,
        max_size=None,
        ping_interval=20,
        ping_timeout=10,
    ):
        print("[SERVER] Ready. Waiting for calls…")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SERVER] Stopped")