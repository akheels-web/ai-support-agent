import asyncio
import base64
import json
import os
import websockets
from dotenv import load_dotenv

load_dotenv("/opt/ai-support-agent/.env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")

ASTERISK_WS_HOST = "127.0.0.1"
ASTERISK_WS_PORT = 8765

OPENAI_WS_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"


SYSTEM_PROMPT = """
You are Arif, an AI IT Support voice agent for National Finance IT Support team.

CALL FLOW:
1. Greeting:
- Say exactly once: "Hi, I am Arif from National Finance IT Support team. Please say Arabic or English to continue."
- Do not ask for the issue before language is selected.

2. Language:
- Wait for the caller to say Arabic or English.
- If caller says English, continue only in English.
- If caller says Arabic, continue only in Arabic.
- Never mix languages.
- If caller says hello before selecting language, say: "Please say Arabic or English to continue."

3. Verification:
- Ask for caller full name.
- Ask for employee ID.
- You must call verify_user with both employee_id and employee_name.
- Only proceed if verify_user returns verified=true.
- If verification fails, ask caller to repeat correct full name and employee ID.
- Caller gets maximum 3 verification attempts.
- If verification fails 3 times, call close_call with reason=verification_failed.
- Do not provide support or create tickets before verification succeeds.

4. Issue handling:
- Ask: "How can I help you today?"
- Ask only one short clarifying question when needed.
- Try basic troubleshooting first.
- Do not immediately create a ticket unless the issue clearly requires escalation.
- Do not overlap or talk over the caller.
- Give one instruction at a time.
- After each troubleshooting step, ask: "Did that resolve the issue?"

5. Ticket creation:
- If unresolved after 3 to 4 useful troubleshooting questions or steps, create a ticket.
- If caller says "not working", "still same", "please create ticket", or similar, create a ticket.
- Before creating ticket, say only once: "Please hold one moment while I create the ticket."
- Then immediately call create_ticket.
- Never say ticket created unless create_ticket returns success=true.
- Never invent ticket numbers.
- If create_ticket returns ticket_number, read it clearly to the caller.
- In English say: "Your ticket number is ..."
- In Arabic say: "رقم التذكرة هو ..."

6. Call ending:
- If caller says they want to disconnect, end the call.
- If caller says bye, thank you, no, nothing else, or disconnect the call, call close_call.
- Always call close_call to end the call.
- Never just say goodbye and stay silent.

IT KNOWLEDGE:
- For VPN TLS error:
  - Ask if TLS error appears before login or after login.
  - If before login, it may be certificate or VPN gateway issue. Create Network Support ticket if not resolved.
  - If after login, it may be expired user certificate or profile issue. Create Network Support ticket.
  - Ask user to confirm internet works.
  - Ask user to try another network only if reasonable.
  - Do not keep troubleshooting forever.
- For account lockout:
  - Do not suggest restart as main solution.
  - Verify caller.
  - Create Service Desk high priority ticket.
- For MFA issue:
  - Verify caller.
  - Create Security or Service Desk high priority ticket.
- For password reset:
  - Do not reset password directly.
  - Verify caller.
  - Create Service Desk ticket.
- For access request:
  - Create ticket because approval is required.

STYLE:
- Keep answers short.
- Phone-friendly sentences only.
- Do not read bullet points aloud.
- Do not repeat yourself.
- Do not speak while waiting for tool result except "Please hold one moment."
"""


TOOLS = [
    {
        "type": "function",
        "name": "verify_user",
        "description": "Verify caller identity by matching full name and employee ID against users.csv.",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "string",
                    "description": "Employee ID provided by caller"
                },
                "employee_name": {
                    "type": "string",
                    "description": "Full name provided by caller"
                }
            },
            "required": ["employee_id", "employee_name"]
        }
    },
    {
        "type": "function",
        "name": "create_ticket",
        "description": "Create a support ticket in Zammad. Only call this after verification succeeds.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_email": {
                    "type": "string"
                },
                "title": {
                    "type": "string"
                },
                "description": {
                    "type": "string"
                },
                "priority": {
                    "type": "string",
                    "enum": ["1 low", "2 normal", "3 high"]
                },
                "group": {
                    "type": "string",
                    "description": "Service Desk, Network Support, Security, or Application Support"
                }
            },
            "required": ["customer_email", "title", "description", "priority", "group"]
        }
    },
    {
        "type": "function",
        "name": "close_call",
        "description": "Terminate the call cleanly. Always call this when the caller wants to end or the flow is complete.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [
                        "resolved",
                        "ticket_created",
                        "verification_failed",
                        "caller_requested",
                        "timeout"
                    ]
                }
            },
            "required": ["reason"]
        }
    }
]


def build_session_config():
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": SYSTEM_PROMPT,
            "output_modalities": ["audio"],
            "tools": TOOLS,
            "tool_choice": "auto",
            "audio": {
                "input": {
                    "format": {
                        "type": "audio/pcmu"
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.50,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 650,
                        "create_response": True,
                        "interrupt_response": True,
                        "idle_timeout_ms": 8000
                    }
                },
                "output": {
                    "format": {
                        "type": "audio/pcmu"
                    },
                    "voice": "alloy"
                }
            }
        }
    }


async def connect_openai():
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    ws = await websockets.connect(
        OPENAI_WS_URL,
        additional_headers=headers,
        max_size=None
    )

    await ws.send(json.dumps(build_session_config()))

    greeting = {
        "type": "response.create",
        "response": {
            "output_modalities": ["audio"],
            "instructions": (
                "Say exactly this and nothing else: "
                "Hi, I am Arif from National Finance IT Support team. "
                "Please say Arabic or English to continue."
            )
        }
    }

    await ws.send(json.dumps(greeting))

    return ws


async def handle_asterisk_call(asterisk_ws):
    state = {
        "verified_user": None,
        "verification_attempts": 0,
        "last_ticket_number": None,
        "call_ending": False,
        "ticket_created": False,
    }

    print("[ASTERISK] New call connected")

    try:
        openai_ws = await connect_openai()
        print("[OPENAI] Connected")
    except Exception as exc:
        print(f"[OPENAI] Connection failed: {exc!r}")
        await asterisk_ws.close()
        return

    async def speak_and_close(reason):
        if state["call_ending"]:
            return

        state["call_ending"] = True

        try:
            await openai_ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "output_modalities": ["audio"],
                    "instructions": (
                        "Say a short goodbye appropriate to the selected language. "
                        "Then stop speaking."
                    )
                }
            }))

            await asyncio.sleep(5)

        except Exception as exc:
            print(f"[CALL] Goodbye failed: {exc!r}")

        try:
            await asterisk_ws.close()
        except Exception:
            pass

        print(f"[CALL] Closed. Reason: {reason}")

    async def execute_tool(tool_name, arguments):
        if tool_name == "verify_user":
            from app.verify import verify_user

            employee_id = arguments.get("employee_id", "").strip()
            employee_name = arguments.get("employee_name", "").strip()

            result = verify_user(employee_id, employee_name)

            if result.get("verified"):
                state["verified_user"] = result
                state["verification_attempts"] = 0
                print(f"[VERIFY] Verified: {result.get('name')} ({employee_id})")
                return {
                    "verified": True,
                    "name": result.get("name"),
                    "email": result.get("email"),
                    "employee_id": result.get("employee_id"),
                    "department": result.get("department")
                }

            state["verification_attempts"] += 1
            attempts_left = 3 - state["verification_attempts"]

            print(f"[VERIFY] Failed attempt {state['verification_attempts']}/3")

            if state["verification_attempts"] >= 3:
                asyncio.create_task(speak_and_close("verification_failed"))

                return {
                    "verified": False,
                    "attempts_left": 0,
                    "action": "call_will_end",
                    "message": "Maximum verification attempts reached."
                }

            return {
                "verified": False,
                "attempts_left": attempts_left,
                "message": "Name and employee ID did not match."
            }

        if tool_name == "create_ticket":
            if not state["verified_user"]:
                return {
                    "success": False,
                    "error": "Caller is not verified. Cannot create ticket."
                }

            from app.zammad_api import create_ticket

            customer_email = state["verified_user"].get("email") or arguments.get("customer_email", "")
            title = arguments.get("title", "IT Support Request")
            description = arguments.get("description", "Issue reported via AI voice agent.")
            priority = arguments.get("priority", "2 normal")
            group = arguments.get("group", "Service Desk")

            try:
                result = create_ticket(
                    customer_email=customer_email,
                    title=title,
                    body=description,
                    group=group,
                    priority=priority
                )

                ticket_number = result.get("ticket_number")

                if ticket_number:
                    state["last_ticket_number"] = ticket_number
                    state["ticket_created"] = True

                    print(f"[ZAMMAD] Ticket created: {ticket_number}")

                    return {
                        "success": True,
                        "ticket_number": ticket_number,
                        "message": "Ticket created successfully. Read ticket number to caller."
                    }

                return {
                    "success": False,
                    "error": "Zammad did not return ticket number."
                }

            except Exception as exc:
                print(f"[ZAMMAD] Ticket creation failed: {exc!r}")

                return {
                    "success": False,
                    "error": str(exc)
                }

        if tool_name == "close_call":
            reason = arguments.get("reason", "caller_requested")
            asyncio.create_task(speak_and_close(reason))

            return {
                "closed": True,
                "reason": reason
            }

        return {
            "success": False,
            "error": f"Unknown tool: {tool_name}"
        }

    async def handle_tool_call(event):
        item = event.get("item", {})
        tool_name = item.get("name", "")
        call_id = item.get("call_id", "")
        arguments_raw = item.get("arguments", "{}")

        try:
            arguments = json.loads(arguments_raw)
        except Exception:
            arguments = {}

        print(f"[TOOL] {tool_name} args={arguments}")

        try:
            result = await execute_tool(tool_name, arguments)
        except Exception as exc:
            print(f"[TOOL ERROR] {tool_name} failed: {repr(exc)}")
            result = {
                "success": False,
                "error": f"Tool {tool_name} failed: {str(exc)}"
    }

        await openai_ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result)
            }
        }))

        if not state["call_ending"]:
            await openai_ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "output_modalities": ["audio"],
                    "instructions": (
                        "Continue based on the tool result. "
                        "If verification failed, ask for correct details again if attempts remain. "
                        "If verification succeeded, continue to support. "
                        "If ticket was created, read the ticket number clearly and ask if anything else is needed. "
                        "If the caller says no, call close_call."
                    )
                }
            }))

    async def asterisk_to_openai():
        try:
            async for message in asterisk_ws:
                if state["call_ending"]:
                    break

                if isinstance(message, bytes):
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(message).decode("utf-8")
                    }))
                else:
                    print(f"[ASTERISK CONTROL] {message}")

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            print(f"[ASTERISK->OPENAI] {exc!r}")

    async def openai_to_asterisk():
        try:
            async for raw in openai_ws:
                if state["call_ending"]:
                    break

                event = json.loads(raw)
                event_type = event.get("type", "")

                if event_type == "response.output_audio.delta":
                    audio_b64 = event.get("delta", "")

                    if audio_b64:
                        await asterisk_ws.send(base64.b64decode(audio_b64))

                elif event_type == "response.output_item.done":
                    item = event.get("item", {})

                    if item.get("type") == "function_call":
                        await handle_tool_call(event)

                elif event_type == "response.done":
                    response = event.get("response", {})
                    print(f"[OPENAI] Response done status={response.get('status')}")

                elif event_type == "error":
                    print(f"[OPENAI ERROR] {json.dumps(event, indent=2)}")

                elif event_type in (
                    "session.created",
                    "session.updated",
                    "response.created",
                    "response.output_item.added",
                    "response.content_part.added",
                    "response.content_part.done",
                    "response.output_audio.done",
                    "response.output_audio_transcript.delta",
                    "response.output_audio_transcript.done",
                    "input_audio_buffer.speech_started",
                    "input_audio_buffer.speech_stopped",
                    "rate_limits.updated",
                    "conversation.item.created",
                    "conversation.item.done",
                ):
                    pass

                else:
                    if event_type:
                        print(f"[OPENAI EVENT] {event_type}")

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            print(f"[OPENAI->ASTERISK] {exc!r}")

    await asyncio.gather(
        asterisk_to_openai(),
        openai_to_asterisk()
    )

    print("[CALL] Handler exiting")


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
        ping_timeout=10
    ):
        print("[SERVER] Ready. Waiting for calls...")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SERVER] Stopped")