import asyncio
import base64
import json
import os
import time
import websockets
from dotenv import load_dotenv

from app.call_logger import create_call, update_call, close_call as log_close_call

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

CRITICAL RULE:
- Follow the call workflow exactly.
- Do not skip steps.
- Do not create a ticket unless caller is verified and caller clearly confirms ticket creation.
- Do not speak in mixed languages.
- Ask one question at a time and wait for the caller's answer.

CALL FLOW:
1. Greet caller once:
   "Hi, I am Arif from National Finance IT Support team. Please say Arabic or English to continue."
2. Wait for caller to choose Arabic or English.
3. Call set_language.
4. Ask caller full name.
5. Call capture_name.
6. Ask employee ID.
7. Call capture_employee_id.
8. Call verify_user.
9. If verified, ask: "How can I help you today?"
10. Collect issue details using record_issue_detail.
11. Troubleshoot with maximum 3 to 4 useful questions or steps.
12. If issue is not resolved, ask:
    "This needs IT team support. Shall I create a ticket for you?"
13. Only if caller says yes / okay / go ahead / create ticket, call confirm_ticket.
14. After confirm_ticket succeeds, call create_ticket.
15. Read ticket number.
16. Ask if anything else is needed.
17. If caller says no / nothing / bye / thank you / disconnect, call close_call.

LANGUAGE RULES:
- If English is selected, speak only English.
- If Arabic is selected, speak only Arabic.
- Never mix Arabic and English.
- After set_language, every response must be in selected language only.
- If unsure, ask the caller to repeat in the selected language.


ARABIC NAME HANDLING:
- If Arabic is selected, continue speaking Arabic to the caller.
- When capturing the caller name for tools, pass employee_name as English Latin transliteration if possible.
- Example: "رقية البلوشي" should be passed as "Ruqaiya Al Balushi" if confidently understood.
- If the name is unclear, do not guess.
- Ask the caller to repeat the full name slowly.
- Never verify based only on family name such as Al Balushi.
- Employee ID must be exact.

VERIFICATION HARD RULE:
- If verify_user returns verified=false, do not proceed.
- Never say the caller is verified unless the tool returns verified=true.
- If Arabic transcription gives a different name than the caller intended, ask the caller to repeat the name slowly.

BACKGROUND NOISE RULE:
- If you hear more than one speaker, background conversation, or unclear audio, do not continue troubleshooting.
- Call report_audio_issue.
- Say: "I am hearing background voices. Please speak one person at a time so I can help you correctly."
- Then wait.

QUESTION HANDLING RULES:
- Ask only one question at a time.
- After asking a question, wait for caller's actual answer.
- Never answer your own question.
- Never assume the answer.
- Never say "got it", "okay", or "understood" unless the caller clearly answered.
- If caller is silent or unclear, ask once: "I did not hear your answer clearly. Could you please repeat?"

VERIFICATION RULES:
- Do not provide IT support before verify_user returns verified=true.
- Do not say verified unless backend returns verified=true.
- If verification fails, ask for name and employee ID again.
- If verification fails 3 times, close the call politely.

TICKET RULES:
- Never say ticket is created unless create_ticket returns success=true.
- Never invent ticket numbers.
- Never call create_ticket before confirm_ticket.
- If create_ticket returns ticket_number, read it clearly.
- In English say: "Your ticket number is ..."
- In Arabic say: "رقم التذكرة هو ..."
- If ticket creation fails, do not retry repeatedly.

IT SUPPORT KNOWLEDGE:
- Account lockout: verify user, ask exact screen/message, create Service Desk high priority ticket after confirmation.
- Password reset: verify user, do not reset directly, create Service Desk ticket after confirmation.
- MFA issue: verify user, ask if phone changed or code not working, create Service Desk/Security ticket after confirmation.
- VPN TLS error: ask before login or after login, confirm internet works, create Network Support ticket after confirmation if unresolved.
- VPN timeout: ask if internet works, ask if websites open, suggest reconnecting VPN once, create Network Support ticket if unresolved.
- Slow laptop: ask when it started, ask if rebooted recently, ask if Task Manager shows high CPU if user can check.
- Laptop not turning on: ask when issue started, ask if charger/laptop lights are visible, ask to hold power button for 15 seconds, then ask if it turns on.
- Printer issue: ask if printer is online, if others can print, if queue is stuck.
- Access denied: verify user and create Application Support ticket after confirmation.

STYLE:
- Keep responses short and phone-friendly.
- No long explanations.
- No bullet points aloud.
- Be calm, professional, and direct.
"""


TOOLS = [
    {
        "type": "function",
        "name": "set_language",
        "description": "Set caller language for this call.",
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
        "name": "capture_name",
        "description": "Capture caller full name before verification.",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_name": {
                    "type": "string"
                }
            },
            "required": ["employee_name"]
        }
    },
    {
        "type": "function",
        "name": "capture_employee_id",
        "description": "Capture caller employee ID before verification.",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "string"
                }
            },
            "required": ["employee_id"]
        }
    },
    {
        "type": "function",
        "name": "verify_user",
        "description": "Verify caller identity using captured or provided full name and employee ID.",
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
        "name": "record_issue_detail",
        "description": "Record caller issue details and troubleshooting answers.",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_category": {
                    "type": "string"
                },
                "field": {
                    "type": "string"
                },
                "value": {
                    "type": "string"
                },
                "summary": {
                    "type": "string"
                }
            },
            "required": ["field", "value"]
        }
    },
    {
        "type": "function",
        "name": "confirm_ticket",
        "description": "Confirm caller clearly agreed to ticket creation.",
        "parameters": {
            "type": "object",
            "properties": {
                "confirmed": {
                    "type": "boolean"
                }
            },
            "required": ["confirmed"]
        }
    },
    {
        "type": "function",
        "name": "create_ticket",
        "description": "Create Zammad ticket only after verification and caller confirmation.",
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
                    "type": "string"
                }
            },
            "required": ["title", "description", "priority", "group"]
        }
    },
    {
        "type": "function",
        "name": "report_audio_issue",
        "description": "Report unclear audio, background voice, or multiple speakers detected.",
        "parameters": {
            "type": "object",
            "properties": {
                "issue": {
                    "type": "string"
                }
            },
            "required": ["issue"]
        }
    },
    {
        "type": "function",
        "name": "close_call",
        "description": "Close call cleanly with goodbye.",
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
                        "audio_unclear",
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
                        "threshold": 0.75,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 1700,
                        "create_response": True,
                        "interrupt_response": False,
                        "idle_timeout_ms": 30000
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


def digit_by_digit(value):
    return " ".join(list(str(value)))


def language_prefix(state):
    if state.get("language") == "ar":
        return "Respond only in Arabic."
    return "Respond only in English."


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
        "current_state": "greeting",

        "language": None,
        "greeted": True,

        "caller_name": None,
        "employee_id": None,
        "verified_user": None,
        "verification_attempts": 0,

        "issue_category": None,
        "issue_summary": None,
        "last_question": None,
        "awaiting_answer": False,
        "question_count": 0,
        "answers_received": [],
        "troubleshooting_steps": [],

        "ticket_confirmed": False,
        "ticket_creation_attempted": False,
        "last_ticket_number": None,
        "ticket_created": False,

        "active_response": False,
        "pending_response_instruction": None,
        "pending_goodbye_instruction": None,
        "close_after_next_response_done": False,

        "tool_in_progress": False,
        "call_ending": False,
        "closing": False,

        "background_noise_warning_count": 0,
    }

    create_call(state["call_id"])
    print("[ASTERISK] New call connected")

    try:
        openai_ws = await connect_openai()
        print("[OPENAI] Connected")
    except Exception as exc:
        print(f"[OPENAI] Connection failed: {exc!r}")
        update_call(state["call_id"], status="openai_connection_failed", summary=str(exc))
        await asterisk_ws.close()
        return

    def queue_response(instruction):
        state["pending_response_instruction"] = instruction

    def queue_goodbye(reason):
        if state["closing"]:
            return

        state["closing"] = True
        state["current_state"] = "closing"

        if state.get("language") == "ar":
            state["pending_goodbye_instruction"] = (
                "Respond only in Arabic. Say exactly: "
                "شكراً لاتصالك بدعم تقنية المعلومات في ناشيونال فاينانس. مع السلامة."
            )
        else:
            state["pending_goodbye_instruction"] = (
                "Respond only in English. Say exactly: "
                "Thank you for calling National Finance IT Support. Goodbye."
            )

        update_call(state["call_id"], status=reason)
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
                state["current_state"] = "ask_name"

                update_call(
                    state["call_id"],
                    language=language,
                    status="language_selected"
                )

                print(f"[LANGUAGE] Selected: {language}")

                return {
                    "success": True,
                    "language": language,
                    "next_state": state["current_state"]
                }

            if tool_name == "capture_name":
                employee_name = arguments.get("employee_name", "").strip()

                if not employee_name:
                    return {
                        "success": False,
                        "error": "Name was empty. Ask caller to repeat full name."
                    }

                state["caller_name"] = employee_name
                state["current_state"] = "ask_employee_id"

                print(f"[CAPTURE] Name: {employee_name}")

                return {
                    "success": True,
                    "employee_name": employee_name,
                    "next_state": state["current_state"]
                }

            if tool_name == "capture_employee_id":
                employee_id = arguments.get("employee_id", "").strip()

                if not employee_id:
                    return {
                        "success": False,
                        "error": "Employee ID was empty. Ask caller to repeat employee ID."
                    }

                state["employee_id"] = employee_id
                state["current_state"] = "verification"

                print(f"[CAPTURE] Employee ID: {employee_id}")

                return {
                    "success": True,
                    "employee_id": employee_id,
                    "next_state": state["current_state"]
                }

            if tool_name == "verify_user":
                from app.verify import verify_user

                employee_id = arguments.get("employee_id", "").strip() or state.get("employee_id")
                employee_name = arguments.get("employee_name", "").strip() or state.get("caller_name")

                if not employee_name or not employee_id:
                    return {
                        "verified": False,
                        "error": "Both employee name and employee ID are required before verification."
                    }

                state["caller_name"] = employee_name
                state["employee_id"] = employee_id

                result = verify_user(employee_id, employee_name)

                if result.get("verified"):
                    state["verified_user"] = result
                    state["verification_attempts"] = 0
                    state["current_state"] = "verified"

                    update_call(
                        state["call_id"],
                        employee_id=result.get("employee_id"),
                        verified_name=result.get("name"),
                        status="verified"
                    )

                    print(f"[VERIFY] Verified: {result.get('name')} ({employee_id})")

                    return {
                        "verified": True,
                        "name": result.get("name"),
                        "email": result.get("email"),
                        "employee_id": result.get("employee_id"),
                        "department": result.get("department"),
                        "next_state": state["current_state"]
                    }

                state["verification_attempts"] += 1
                attempts_left = 3 - state["verification_attempts"]

                print(f"[VERIFY] Failed attempt {state['verification_attempts']}/3")

                if state["verification_attempts"] >= 3:
                    state["current_state"] = "closing"
                    update_call(state["call_id"], status="verification_failed")
                    queue_goodbye("verification_failed")

                    return {
                        "verified": False,
                        "attempts_left": 0,
                        "action": "call_will_end",
                        "message": "Maximum verification attempts reached."
                    }

                state["current_state"] = "ask_name"

                return {
                    "verified": False,
                    "attempts_left": attempts_left,
                    "message": "Name and employee ID did not match.",
                    "next_state": state["current_state"]
                }

            if tool_name == "record_issue_detail":
                if not state["verified_user"]:
                    return {
                        "success": False,
                        "error": "Caller is not verified. Cannot collect support details yet."
                    }

                issue_category = arguments.get("issue_category", "").strip()
                field = arguments.get("field", "").strip()
                value = arguments.get("value", "").strip()
                summary = arguments.get("summary", "").strip()

                if issue_category:
                    state["issue_category"] = issue_category

                if summary:
                    state["issue_summary"] = summary
                elif not state["issue_summary"] and value:
                    state["issue_summary"] = value

                if field and value:
                    state["answers_received"].append({
                        "field": field,
                        "value": value
                    })

                    state["troubleshooting_steps"].append(f"{field}: {value}")

                state["question_count"] += 1
                state["awaiting_answer"] = False
                state["current_state"] = "troubleshooting"

                update_call(
                    state["call_id"],
                    status="troubleshooting",
                    summary=state.get("issue_summary") or value
                )

                print(f"[ISSUE] {field}: {value}")

                return {
                    "success": True,
                    "issue_category": state.get("issue_category"),
                    "issue_summary": state.get("issue_summary"),
                    "question_count": state["question_count"],
                    "max_questions": 4,
                    "next_state": state["current_state"]
                }

            if tool_name == "confirm_ticket":
                confirmed = bool(arguments.get("confirmed"))

                if not state["verified_user"]:
                    return {
                        "success": False,
                        "error": "Caller is not verified. Cannot confirm ticket."
                    }

                if not state["issue_summary"]:
                    return {
                        "success": False,
                        "error": "Issue summary is missing. Ask one more question before ticket confirmation."
                    }

                if confirmed:
                    state["ticket_confirmed"] = True
                    state["current_state"] = "ticket_creation"

                    print("[TICKET] Caller confirmed ticket creation")

                    return {
                        "success": True,
                        "ticket_confirmed": True,
                        "next_state": state["current_state"]
                    }

                state["ticket_confirmed"] = False
                state["current_state"] = "wrap_up"

                return {
                    "success": True,
                    "ticket_confirmed": False,
                    "message": "Caller declined ticket creation.",
                    "next_state": state["current_state"]
                }

            if tool_name == "create_ticket":
                if not state["verified_user"]:
                    return {
                        "success": False,
                        "error": "Ticket blocked. Caller is not verified."
                    }

                if not state["issue_summary"]:
                    return {
                        "success": False,
                        "error": "Ticket blocked. Issue summary is missing."
                    }

                if not state["ticket_confirmed"]:
                    state["current_state"] = "ticket_confirmation"

                    return {
                        "success": False,
                        "error": "Ticket creation blocked. Caller confirmation is required first.",
                        "required_action": "Ask caller if they want a ticket created."
                    }

                if state["ticket_creation_attempted"]:
                    return {
                        "success": False,
                        "error": "Ticket creation was already attempted. Do not retry."
                    }

                state["ticket_creation_attempted"] = True

                from app.zammad_api import create_ticket

                verified_user = state["verified_user"]
                customer_email = verified_user.get("email")

                title = arguments.get("title", "IT Support Request")
                description = arguments.get("description", state["issue_summary"] or "Issue reported via AI voice agent.")
                priority = arguments.get("priority", "2 normal")
                group = arguments.get("group", "Service Desk")

                key_points = "\n".join([f"- {item['field']}: {item['value']}" for item in state["answers_received"][-10:]])
                troubleshooting = "\n".join([f"- {step}" for step in state["troubleshooting_steps"][-10:]])

                ticket_body = (
                    f"Caller: {verified_user.get('name')}\n"
                    f"Employee ID: {verified_user.get('employee_id')}\n"
                    f"Email: {verified_user.get('email')}\n"
                    f"Department: {verified_user.get('department')}\n\n"
                    f"Issue Summary:\n{description}\n\n"
                    f"Key Points Collected:\n{key_points if key_points else '- No extra key points captured'}\n\n"
                    f"Troubleshooting / Questions:\n{troubleshooting if troubleshooting else '- No troubleshooting steps captured'}\n\n"
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
                        state["current_state"] = "wrap_up"

                        update_call(
                            state["call_id"],
                            ticket_number=ticket_number,
                            ticket_created=1,
                            status="ticket_created",
                            summary=description
                        )

                        print(f"[ZAMMAD] Ticket created: {ticket_number}")

                        return {
                            "success": True,
                            "ticket_number": ticket_number,
                            "ticket_number_spoken": digit_by_digit(ticket_number),
                            "message": "Ticket created successfully.",
                            "next_state": state["current_state"]
                        }

                    update_call(
                        state["call_id"],
                        status="ticket_failed",
                        summary="Zammad did not return ticket number."
                    )

                    return {
                        "success": False,
                        "error": "Zammad did not return ticket number."
                    }

                except Exception as exc:
                    print(f"[ZAMMAD] Ticket creation failed: {exc!r}")

                    update_call(
                        state["call_id"],
                        status="ticket_failed",
                        summary=str(exc)
                    )

                    return {
                        "success": False,
                        "error": str(exc)
                    }

            if tool_name == "report_audio_issue":
                issue = arguments.get("issue", "unclear_audio")
                state["background_noise_warning_count"] += 1

                print(f"[AUDIO] Issue reported: {issue}. Count={state['background_noise_warning_count']}")

                if state["background_noise_warning_count"] >= 3:
                    queue_goodbye("audio_unclear")

                    return {
                        "success": True,
                        "action": "call_will_end",
                        "message": "Audio remains unclear after multiple warnings."
                    }

                return {
                    "success": True,
                    "warning_count": state["background_noise_warning_count"],
                    "message": "Ask caller to speak one person at a time and move to a quieter place."
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

        prefix = language_prefix(state)

        if tool_name == "set_language":
            if state["language"] == "ar":
                queue_response("Respond only in Arabic. Confirm Arabic briefly and ask for the caller's full name.")
            else:
                queue_response("Respond only in English. Confirm English briefly and ask for the caller's full name.")

        elif tool_name == "capture_name" and result.get("success"):
            queue_response(f"{prefix} Ask for the caller's employee ID. Keep it short.")

        elif tool_name == "capture_employee_id" and result.get("success"):
            queue_response(f"{prefix} Say please wait while I verify your details, then call verify_user immediately.")

        elif tool_name == "verify_user" and result.get("verified"):
            queue_response(f"{prefix} Say the caller is verified. Then ask: How can I help you today?")

        elif tool_name == "verify_user" and not result.get("verified"):
            if result.get("attempts_left", 0) > 0:
                queue_response(
                    f"{prefix} Say the details did not match. "
                    f"Ask for the correct full name and employee ID again. "
                    f"Mention they have {result.get('attempts_left')} attempt remaining."
                )

        elif tool_name == "record_issue_detail" and result.get("success"):
            if state["question_count"] >= 4:
                queue_response(
                    f"{prefix} Say this needs IT team support. Ask exactly: Shall I create a ticket for you?"
                )
            else:
                queue_response(
                    f"{prefix} Continue troubleshooting. Ask only one short useful question or give one safe step. "
                    f"Wait for the caller's answer."
                )

        elif tool_name == "confirm_ticket" and result.get("ticket_confirmed"):
            queue_response(
                f"{prefix} Say please hold one moment while I create the ticket. Then call create_ticket immediately."
            )

        elif tool_name == "confirm_ticket" and not result.get("ticket_confirmed"):
            queue_response(f"{prefix} Ask if there is anything else you can help with.")

        elif tool_name == "create_ticket" and result.get("success") and result.get("ticket_number"):
            ticket_number = result.get("ticket_number")
            ticket_spoken = result.get("ticket_number_spoken") or ticket_number

            if state["language"] == "ar":
                queue_response(
                    f"Respond only in Arabic. Say the ticket was created successfully. "
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
            if result.get("required_action"):
                queue_response(
                    f"{prefix} Say this needs IT team support. Ask exactly: Shall I create a ticket for you?"
                )
            else:
                queue_response(
                    f"{prefix} Say there is a technical issue creating the ticket right now. "
                    f"Ask the caller to contact IT support directly. Do not retry."
                )

        elif tool_name == "report_audio_issue":
            if result.get("action") == "call_will_end":
                pass
            else:
                queue_response(
                    f"{prefix} Say: I am hearing background voices. Please speak one person at a time so I can help you correctly."
                )

        elif tool_name == "close_call":
            pass

        else:
            queue_response(f"{prefix} Continue based on the tool result. Keep it short.")

        await send_queued_response_if_any()

    async def asterisk_to_openai():
        try:
            async for message in asterisk_ws:
                if state["call_ending"]:
                    break

                if state["tool_in_progress"] or state["closing"] or state["active_response"]:
                    continue

                if isinstance(message, bytes):
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(message).decode("utf-8")
                    }))
                else:
                    print(f"[ASTERISK CONTROL] {message}")

                    if isinstance(message, str) and "MEDIA_START" in message:
                        parts = message.split()

                        for part in parts:
                            if part.startswith("channel_id:"):
                                real_call_id = part.replace("channel_id:", "").strip()

                                if real_call_id:
                                    state["call_id"] = real_call_id
                                    create_call(state["call_id"])
                                    update_call(state["call_id"], status="in_progress")
                                    print(f"[CALL] Asterisk call ID detected: {real_call_id}")
                                    break

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
                        update_call(state["call_id"], status="openai_response_failed")
                        try:
                            await asterisk_ws.close()
                        except Exception:
                            pass
                        return

                    if state["close_after_next_response_done"]:
                        await asyncio.sleep(2)
                        state["call_ending"] = True

                        log_close_call(state["call_id"], status="completed")

                        try:
                            await asterisk_ws.close()
                        except Exception:
                            pass

                        return

                    await send_queued_response_if_any()

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    if transcript:
                        print(f"[CALLER SAID] {transcript}")

                elif event_type == "error":
                    print(f"[OPENAI ERROR] {json.dumps(event, indent=2)}")

                    error = event.get("error", {})
                    if error.get("code") == "conversation_already_has_active_response":
                        state["active_response"] = True

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

    if not state["call_ending"]:
        log_close_call(state["call_id"], status="ended")


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