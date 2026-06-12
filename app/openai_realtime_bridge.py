import asyncio
import base64
import json
import os
import time
import websockets
from dotenv import load_dotenv

load_dotenv("/opt/ai-support-agent/.env", override=True)

OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip().strip('"').strip("'")
OPENAI_REALTIME_MODEL = (os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime") or "gpt-realtime").strip()

ASTERISK_WS_HOST = "127.0.0.1"
ASTERISK_WS_PORT = 8765

MAX_CONCURRENT_CALLS = int(os.getenv("MAX_CONCURRENT_CALLS", "5"))
ACTIVE_CALLS = 0
ACTIVE_CALLS_LOCK = asyncio.Lock()

OPENAI_WS_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

SYSTEM_PROMPT = """
You are Arif, an AI IT Support voice agent for National Finance IT Support team.

STRICT CALL FLOW:
1. Say exactly once: "Hi, I am Arif from National Finance IT Support team. Please say Arabic or English to continue."
2. Wait for caller to choose Arabic or English.
3. You must call set_language after caller chooses language.
4. Ask for caller full name.
5. Ask for employee ID.
6. You must call verify_user with both employee_name and employee_id.
7. Do not provide IT support until verify_user returns verified=true.
8. If verification fails, ask caller to repeat correct full name and employee ID.
9. Caller gets maximum 3 verification attempts.
10. If verification fails 3 times, call close_call with reason=verification_failed.
11. After verification succeeds, ask: "How can I help you today?"
12. Troubleshoot with max 3 to 4 short questions or steps.
13. If issue is solved, ask if anything else is needed.
14. If issue is not solved, create a ticket.
15. If caller asks to disconnect, says bye, says no, says nothing else, or says thank you, call close_call.

LANGUAGE RULES:
- If English is selected, speak only English.
- If Arabic is selected, speak only Arabic.
- Never mix Arabic and English.
- Ticket number must be spoken in selected language.
- Goodbye must be spoken in selected language.

VERIFICATION RULES:
- Employee ID must match exactly.
- Full name must match or be close enough based on backend verification.
- Do not say verified unless verify_user returns verified=true.
- Do not create a verified ticket unless user is verified.

TICKET RULES:
- Never say ticket is created unless create_ticket returns success=true.
- Never invent ticket numbers.
- If create_ticket returns ticket_number, read it clearly once.
- In English say: "Your ticket number is ..."
- In Arabic say: "رقم التذكرة هو ..."
- After reading ticket number, ask if anything else is needed.
- If ticket creation fails, do not retry repeatedly. Say there is a technical issue and ask caller to contact IT support directly.

TROUBLESHOOTING RULES:
- Do not jump directly to ticket unless the issue requires escalation.
- Ask one question at a time.
- Do not read bullet points.
- Do not talk over the caller.
- Keep answers short and phone-friendly.

BASIC IT SUPPORT KNOWLEDGE:
- Account lockout: do not suggest restart as main fix. Verify user, ask what exact screen says, then create Service Desk high priority ticket.
- Password reset: do not reset password directly. Verify user, then create Service Desk ticket.
- MFA issue: verify user, ask if phone was changed or code not working, then create Service Desk or Security ticket.
- VPN TLS error: ask if TLS error appears before or after login. Ask if internet works. If still failing, create Network Support ticket.
- VPN timeout: ask internet status, ask if other websites work, suggest reconnecting VPN once. If still failing, create Network Support ticket.
- Slow laptop: ask when issue started, ask if rebooted recently, ask Task Manager high CPU if user can check. If still unresolved, create Service Desk ticket.
- Printer issue: ask if printer is online, ask if others can print, ask if queue is stuck. If unresolved, create Service Desk ticket.
- Software access denied: verify user, create Application Support ticket.
"""


TOOLS = [
    {
        "type": "function",
        "name": "set_language",
        "description": "Set caller language for the current call.",
        "parameters": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "enum": ["en", "ar"]
                }
            },
            "required": ["language"]
        }
    },
    {
        "type": "function",
        "name": "verify_user",
        "description": "Verify caller identity by matching full name and employee ID against users.csv.",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "string"
                },
                "employee_name": {
                    "type": "string"
                }
            },
            "required": ["employee_id", "employee_name"]
        }
    },
    {
        "type": "function",
        "name": "create_ticket",
        "description": "Create a support ticket in Zammad. Only call after verification succeeds.",
        "parameters": {
            "type": "object",
            "properties": {
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
                    "description": "Service Desk, Network Support, Security, Application Support"
                }
            },
            "required": ["title", "description", "priority", "group"]
        }
    },
    {
        "type": "function",
        "name": "close_call",
        "description": "Terminate the call cleanly. Always use this to end the call.",
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
                        "threshold": 0.55,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 900,
                        "create_response": True,
                        "interrupt_response": False,
                        "idle_timeout_ms": 20000
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


async def send_response(openai_ws, instructions):
    await openai_ws.send(json.dumps({
        "type": "response.create",
        "response": {
            "output_modalities": ["audio"],
            "instructions": instructions
        }
    }))


async def connect_openai():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is empty or missing in /opt/ai-support-agent/.env")

    if not OPENAI_API_KEY.startswith("sk-"):
        raise RuntimeError("OPENAI_API_KEY format looks invalid")

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    ws = await websockets.connect(
        OPENAI_WS_URL,
        additional_headers=headers,
        max_size=None
    )

    await ws.send(json.dumps(build_session_config()))

    await send_response(
        ws,
        (
            "Say exactly this and nothing else: "
            "Hi, I am Arif from National Finance IT Support team. "
            "Please say Arabic or English to continue."
        )
    )

    return ws


def _digit_by_digit(value):
    return " ".join(list(str(value)))


async def handle_asterisk_call(asterisk_ws):
    global ACTIVE_CALLS

    async with ACTIVE_CALLS_LOCK:
        if ACTIVE_CALLS >= MAX_CONCURRENT_CALLS:
            print("[CALL] Max concurrent calls reached. Rejecting call.")
            await asterisk_ws.close()
            return

        ACTIVE_CALLS += 1
        print(f"[CALL] Accepted. Active calls: {ACTIVE_CALLS}")

    try:
        await handle_single_call(asterisk_ws)
    finally:
        async with ACTIVE_CALLS_LOCK:
            ACTIVE_CALLS -= 1
            print(f"[CALL] Handler exiting. Active calls: {ACTIVE_CALLS}")


async def handle_single_call(asterisk_ws):
    state = {
        "call_id": str(int(time.time() * 1000)),
        "language": None,
        "verified_user": None,
        "verification_attempts": 0,
        "last_ticket_number": None,
        "ticket_created": False,
        "ticket_creation_attempted": False,
        "tool_in_progress": False,
        "call_ending": False,
        "closing": False,
        "active_response": False,
        "pending_response_instruction": None,
        "pending_goodbye_instruction": None,
        "close_after_next_response_done": False,
        "issue_notes": [],
    }

    print("[ASTERISK] New call connected")

    try:
        openai_ws = await connect_openai()
        print("[OPENAI] Connected")
    except Exception as exc:
        print(f"[OPENAI] Connection failed: {exc!r}")
        await asterisk_ws.close()
        return

    def queue_response(instruction):
        state["pending_response_instruction"] = instruction

    def queue_goodbye(reason):
        if state["closing"]:
            return

        state["closing"] = True

        language = state.get("language") or "en"

        if language == "ar":
            state["pending_goodbye_instruction"] = (
                "Say exactly in Arabic: شكراً لاتصالك بدعم تقنية المعلومات في ناشيونال فاينانس. مع السلامة."
            )
        else:
            state["pending_goodbye_instruction"] = (
                "Say exactly: Thank you for calling National Finance IT Support. Goodbye."
            )

        print(f"[CALL] Goodbye queued. Reason: {reason}")

    async def send_queued_response_if_any():
        if state["call_ending"]:
            return

        if state["active_response"]:
            return

        if state["pending_goodbye_instruction"]:
            instruction = state["pending_goodbye_instruction"]
            state["pending_goodbye_instruction"] = None
            state["close_after_next_response_done"] = True
            state["active_response"] = True
            await send_response(openai_ws, instruction)
            return

        if state["pending_response_instruction"]:
            instruction = state["pending_response_instruction"]
            state["pending_response_instruction"] = None
            state["active_response"] = True
            await send_response(openai_ws, instruction)
            return

    async def execute_tool(tool_name, arguments):
        state["tool_in_progress"] = True

        try:
            if tool_name == "set_language":
                language = arguments.get("language")

                if language not in ("en", "ar"):
                    language = "en"

                state["language"] = language

                print(f"[LANGUAGE] Selected: {language}")

                return {
                    "success": True,
                    "language": language
                }

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
                    queue_goodbye("verification_failed")

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
                if state["ticket_creation_attempted"]:
                    return {
                        "success": False,
                        "error": "Ticket creation was already attempted. Do not retry."
                    }

                state["ticket_creation_attempted"] = True

                if not state["verified_user"]:
                    return {
                        "success": False,
                        "error": "Caller is not verified. Cannot create ticket."
                    }

                from app.zammad_api import create_ticket

                verified_user = state["verified_user"]
                customer_email = verified_user.get("email")
                title = arguments.get("title", "IT Support Request")
                description = arguments.get("description", "Issue reported via AI voice agent.")
                priority = arguments.get("priority", "2 normal")
                group = arguments.get("group", "Service Desk")

                key_points = "\n".join([f"- {note}" for note in state["issue_notes"][-8:]])

                ticket_body = (
                    f"Caller: {verified_user.get('name')}\n"
                    f"Employee ID: {verified_user.get('employee_id')}\n"
                    f"Email: {verified_user.get('email')}\n"
                    f"Department: {verified_user.get('department')}\n\n"
                    f"Issue Summary:\n{description}\n\n"
                    f"Key Points Collected:\n{key_points if key_points else '- No extra key points captured'}\n\n"
                    f"Created by: AI Voice Agent Arif"
                )

                try:
                    result = create_ticket(
                        customer_email=customer_email,
                        title=title,
                        body=ticket_body,
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
                            "ticket_number_spoken": _digit_by_digit(ticket_number),
                            "message": "Ticket created successfully."
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
                queue_goodbye(reason)

                return {
                    "closed": True,
                    "reason": reason
                }

            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}"
            }

        finally:
            state["tool_in_progress"] = False

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

        language = state.get("language") or "en"

        if language == "ar":
            language_instruction = "Respond only in Arabic."
        else:
            language_instruction = "Respond only in English."

        if tool_name == "create_ticket" and result.get("success") and result.get("ticket_number"):
            ticket_number = result.get("ticket_number")
            ticket_spoken = result.get("ticket_number_spoken") or ticket_number

            if language == "ar":
                queue_response(
                    f"Respond only in Arabic. Say that the ticket was created successfully. "
                    f"Say: رقم التذكرة هو {ticket_spoken}. "
                    f"Then ask if the caller needs anything else."
                )
            else:
                queue_response(
                    f"Respond only in English. Say exactly: "
                    f"Your ticket has been created successfully. Your ticket number is {ticket_spoken}. "
                    f"Is there anything else I can help you with?"
                )

        elif tool_name == "create_ticket" and not result.get("success"):
            if language == "ar":
                queue_response(
                    "Respond only in Arabic. Say briefly that there is a technical issue creating the ticket right now. "
                    "Ask the caller to contact IT support directly. Do not retry creating the ticket."
                )
            else:
                queue_response(
                    "Respond only in English. Say exactly: "
                    "I am unable to create the ticket right now due to a technical issue. "
                    "Please contact IT support directly. Do not retry creating the ticket."
                )

        elif tool_name == "verify_user" and result.get("verified"):
            queue_response(
                f"{language_instruction} Say the caller is verified. Then ask: How can I help you today?"
            )

        elif tool_name == "verify_user" and not result.get("verified"):
            if result.get("attempts_left", 0) > 0:
                queue_response(
                    f"{language_instruction} Say the details did not match. "
                    f"Ask for the correct full name and employee ID again. "
                    f"Mention they have {result.get('attempts_left')} attempt remaining."
                )

        elif tool_name == "set_language":
            if state.get("language") == "ar":
                queue_response(
                    "Respond only in Arabic. Confirm Arabic briefly and ask for the caller's full name."
                )
            else:
                queue_response(
                    "Respond only in English. Confirm English briefly and ask for the caller's full name."
                )

        elif tool_name == "close_call":
            pass

        else:
            queue_response(
                f"{language_instruction} Continue based on the tool result. Keep it short."
            )

        await send_queued_response_if_any()

    async def asterisk_to_openai():
        try:
            async for message in asterisk_ws:
                if state["call_ending"]:
                    break

                if state["tool_in_progress"] or state["closing"]:
                    continue

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
                event = json.loads(raw)
                event_type = event.get("type", "")

                if event_type == "response.created":
                    state["active_response"] = True

                elif event_type == "response.output_audio.delta":
                    if state["call_ending"]:
                        break

                    audio_b64 = event.get("delta", "")

                    if audio_b64:
                        await asterisk_ws.send(base64.b64decode(audio_b64))

                elif event_type == "response.output_item.done":
                    item = event.get("item", {})

                    if item.get("type") == "function_call":
                        await handle_tool_call(event)

                elif event_type == "response.done":
                    response = event.get("response", {})
                    status = response.get("status")
                    print(f"[OPENAI] Response done status={status}")

                    state["active_response"] = False

                    if status == "failed":
                        print("[OPENAI] Response failed. Closing this call to avoid blank call.")
                        state["call_ending"] = True
                        try:
                            await asterisk_ws.close()
                        except Exception:
                            pass
                        return

                    if state["close_after_next_response_done"]:
                        await asyncio.sleep(2)
                        state["call_ending"] = True

                        try:
                            await asterisk_ws.close()
                        except Exception:
                            pass

                        return

                    await send_queued_response_if_any()

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    if transcript:
                        state["issue_notes"].append(transcript)
                        print(f"[CALLER SAID] {transcript}")

                elif event_type == "error":
                    print(f"[OPENAI ERROR] {json.dumps(event, indent=2)}")

                elif event_type in (
                    "session.created",
                    "session.updated",
                    "response.output_item.added",
                    "response.content_part.added",
                    "response.content_part.done",
                    "response.output_audio.done",
                    "response.output_audio_transcript.delta",
                    "response.output_audio_transcript.done",
                    "input_audio_buffer.speech_started",
                    "input_audio_buffer.speech_stopped",
                    "input_audio_buffer.committed",
                    "input_audio_buffer.timeout_triggered",
                    "rate_limits.updated",
                    "conversation.item.created",
                    "conversation.item.added",
                    "conversation.item.done",
                    "response.function_call_arguments.delta",
                    "response.function_call_arguments.done",
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


async def main():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing in /opt/ai-support-agent/.env")

    print(f"[SERVER] Starting on {ASTERISK_WS_HOST}:{ASTERISK_WS_PORT}")
    print(f"[SERVER] Model: {OPENAI_REALTIME_MODEL}")
    print(f"[SERVER] Max concurrent calls: {MAX_CONCURRENT_CALLS}")

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