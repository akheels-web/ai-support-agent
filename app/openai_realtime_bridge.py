import asyncio
import base64
import json
import os
import time
from pathlib import Path

import websockets
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = os.getenv("ENV_FILE", str(BASE_DIR / ".env"))
load_dotenv(env_path, override=True)

from app.security_guard import check_rate_limit, log_security_event, reset_rate_limit, is_locked
from app.config import (
    validate_bridge_config,
    OPENAI_API_KEY,
    OPENAI_REALTIME_MODEL,
    MAX_CONCURRENT_CALLS,
    CALL_MAX_SECONDS,
    VAD_THRESHOLD,
    VAD_SILENCE_MS,
    VAD_IDLE_TIMEOUT_MS,
    ASTERISK_QUEUE_STANDARD,
    ASTERISK_QUEUE_EXECUTIVE,
    ASTERISK_QUEUE_EMERGENCY,
)
from app.call_logger import (
    create_call,
    rename_call_id,
    update_call,
    close_call as log_close_call,
    reconcile_stale_calls,
)
from app.verify import verify_user, lookup_caller_by_phone
from app.transfer import transfer_call
from app.ticketing import get_ticketing_client

CALLS_PER_NUMBER_LIMIT = int(os.getenv("CALLS_PER_NUMBER_LIMIT", "5"))
CALLS_PER_NUMBER_WINDOW = int(os.getenv("CALLS_PER_NUMBER_WINDOW", "600"))
CALLS_PER_NUMBER_LOCK = int(os.getenv("CALLS_PER_NUMBER_LOCK", "900"))

VERIFY_FAIL_LIMIT = int(os.getenv("VERIFY_FAIL_LIMIT", "5"))
VERIFY_FAIL_WINDOW = int(os.getenv("VERIFY_FAIL_WINDOW", "3600"))
VERIFY_FAIL_LOCK = int(os.getenv("VERIFY_FAIL_LOCK", "3600"))

ASTERISK_WS_HOST = "127.0.0.1"
ASTERISK_WS_PORT = 8765

ACTIVE_CALLS = 0
ACTIVE_CALLS_LOCK = asyncio.Lock()

OPENAI_WS_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

AUTO_TICKET_CATEGORIES = {
    "keyboard_issue",
    "mouse_issue",
    "hardware_issue",
    "hardware_damage",
    "printer_replacement",
    "monitor_issue",
    "dock_issue",
    "accessory_issue",
    "charger_issue",
    "headset_issue",
}

EMERGENCY_KEYWORDS = {
    "outage", "system down", "core banking", "ransomware", "hacked", "breach",
    "data center", "datacenter", "fire", "server down", "network down",
    "branch down", "payment gateway", "emergency", "طوارئ", "توقف النظام", "النظام متعطل",
}


def load_knowledge_base():
    kb = {}
    kb_dir = BASE_DIR / "knowledge_base"
    if kb_dir.is_dir():
        for file in kb_dir.glob("*.md"):
            try:
                kb[file.stem.lower()] = file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[KB] Error loading {file}: {e}")
    return kb


KNOWLEDGE_BASE = load_knowledge_base()

SYSTEM_PROMPT = """
You are Arif, an AI IT Support voice agent for National Finance IT Support team.

CRITICAL RULES:
- Greet the caller professionally. Bilingual English and Arabic.
- Ask one question at a time and wait for the caller's answer.
- Do not speak in mixed languages.
- Be phone-friendly, calm, and concise.

EXECUTIVE / MANAGEMENT RULE (CEO, CFO, C-SUITE):
- If the system indicates the caller is an Executive (CEO, CFO, C-Level), treat with top priority concierge.
- Greet them with utmost respect: "Welcome to National Finance IT Support. I am transferring you directly to our Senior Executive Support Desk right now."
- Immediately call transfer_to_agent with queue_type="executive".
- Do not make executives undergo routine diagnostic troubleshooting.

EMERGENCY / SEV-1 CRITICAL INCIDENT RULE:
- If caller reports a major emergency or outage (e.g. core banking down, branch offline, ransomware, payment gateway outage, fire, data center alert):
- Do not perform slow troubleshooting or ask routine questions.
- Say: "Understood. This is flagged as a critical incident. I am transferring you immediately to our on-call emergency engineering team and raising an emergency ticket."
- Call escalate_emergency immediately with reason and incident_summary.

SMALL IT ISSUES & TROUBLESHOOTING RULE:
- For common issues (Account locked, Password reset, VPN issues, Slow PC):
- Use lookup_knowledge_base to retrieve verified steps.
- Offer max 2-3 practical, safe troubleshooting steps.
- If the caller says it works now / resolved:
  - Call record_resolution to log a resolved ticket in the helpdesk for SLA/telemetry.
  - Say: "Glad that resolved it! I have recorded reference ticket [number]. Have a great day."
- If the caller needs hardware replacement (mouse, keyboard, monitor, dock) or unresolved issue:
  - Call create_ticket directly.
  - Give caller their ticket number clearly.

STANDARD CALL FLOW:
1. Greet caller: "Hi, I am Arif from National Finance IT Support team. Please say Arabic or English to continue."
2. Caller selects language -> call set_language.
3. If caller is not pre-identified:
   - Ask caller full name -> call capture_name.
   - Ask employee ID -> call capture_employee_id.
   - Call verify_user.
4. If verified, ask: "How can I help you today?"
5. If caller describes issue, call record_issue_detail.
6. Troubleshoot or dispatch ticket.
7. Close call cleanly with close_call.
"""

TOOLS = [
    {
        "type": "function",
        "name": "set_language",
        "description": "Set caller language for this call.",
        "parameters": {
            "type": "object",
            "properties": {"language": {"type": "string", "enum": ["en", "ar"]}},
            "required": ["language"],
        },
    },
    {
        "type": "function",
        "name": "capture_name",
        "description": "Capture caller full name before verification.",
        "parameters": {
            "type": "object",
            "properties": {"employee_name": {"type": "string"}},
            "required": ["employee_name"],
        },
    },
    {
        "type": "function",
        "name": "capture_employee_id",
        "description": "Capture caller employee ID before verification.",
        "parameters": {
            "type": "object",
            "properties": {"employee_id": {"type": "string"}},
            "required": ["employee_id"],
        },
    },
    {
        "type": "function",
        "name": "verify_user",
        "description": "Verify caller identity using full name and employee ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
                "employee_name": {"type": "string"},
            },
            "required": ["employee_id", "employee_name"],
        },
    },
    {
        "type": "function",
        "name": "lookup_knowledge_base",
        "description": "Fetch verified IT troubleshooting steps for account lockouts, password resets, VPN, or hardware.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Issue topic keyword e.g. account_locked, password_reset, vpn_issue, hardware"
                }
            },
            "required": ["topic"],
        },
    },
    {
        "type": "function",
        "name": "record_issue_detail",
        "description": "Record caller issue details and troubleshooting answers.",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_category": {"type": "string"},
                "field": {"type": "string"},
                "value": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["field", "value"],
        },
    },
    {
        "type": "function",
        "name": "record_resolution",
        "description": "Record that caller issue was successfully resolved on call and create a resolved ticket for IT telemetry.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "resolution_summary": {"type": "string"},
            },
            "required": ["title", "resolution_summary"],
        },
    },
    {
        "type": "function",
        "name": "confirm_ticket",
        "description": "Confirm caller clearly agreed to ticket creation.",
        "parameters": {
            "type": "object",
            "properties": {"confirmed": {"type": "boolean"}},
            "required": ["confirmed"],
        },
    },
    {
        "type": "function",
        "name": "create_ticket",
        "description": "Create helpdesk ticket in Frappe Helpdesk.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["1 low", "2 normal", "3 high", "4 urgent"]},
                "group": {"type": "string"},
            },
            "required": ["title", "description", "priority"],
        },
    },
    {
        "type": "function",
        "name": "escalate_emergency",
        "description": "Immediately escalate Sev-1 critical outages or emergency incidents to on-call emergency queue.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "incident_summary": {"type": "string"},
            },
            "required": ["reason", "incident_summary"],
        },
    },
    {
        "type": "function",
        "name": "transfer_to_agent",
        "description": "Transfer call to human IT support queue (standard, executive, or emergency).",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "queue_type": {
                    "type": "string",
                    "enum": ["standard", "executive", "emergency"],
                    "description": "Routing target: standard (7001), executive (7002), or emergency (7003)"
                },
            },
            "required": ["reason"],
        },
    },
    {
        "type": "function",
        "name": "report_audio_issue",
        "description": "Report unclear audio, background voice, or multiple speakers detected.",
        "parameters": {
            "type": "object",
            "properties": {"issue": {"type": "string"}},
            "required": ["issue"],
        },
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
                        "timeout",
                    ],
                }
            },
            "required": ["reason"],
        },
    },
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
                    "format": {"type": "audio/pcmu"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": VAD_THRESHOLD,
                        "prefix_padding_ms": 200,
                        "silence_duration_ms": VAD_SILENCE_MS,
                        "create_response": True,
                        "interrupt_response": True,  # Full-duplex barge-in enabled
                        "idle_timeout_ms": VAD_IDLE_TIMEOUT_MS,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": "alloy",
                },
            },
        },
    }


async def send_response(openai_ws, instructions):
    await openai_ws.send(
        json.dumps(
            {
                "type": "response.create",
                "response": {
                    "output_modalities": ["audio"],
                    "instructions": instructions,
                },
            }
        )
    )


async def connect_openai():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is empty or missing in .env")

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}

    ws = await websockets.connect(
        OPENAI_WS_URL,
        additional_headers=headers,
        max_size=None,
    )

    await ws.send(json.dumps(build_session_config()))

    await send_response(
        ws,
        (
            "Say exactly this and nothing else: "
            "Hi, I am Arif from National Finance IT Support team. "
            "Please say Arabic or English to continue."
        ),
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
        "is_vip": False,
        "is_executive": False,
        "tier": "STANDARD",
        "issue_category": None,
        "issue_summary": None,
        "last_question": None,
        "awaiting_answer": False,
        "question_count": 0,
        "answers_received": [],
        "troubleshooting_steps": [],
        "simple_issue_detected": False,
        "ticket_confirmed": True,
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
        "asterisk_channel": None,
        "caller_number": None,
        "transfer_attempted": False,
        "transfer_target": ASTERISK_QUEUE_STANDARD,
        "started_monotonic": time.monotonic(),
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
                update_call(state["call_id"], language=language, status="language_selected")
                print(f"[LANGUAGE] Selected: {language}")

                # If caller was pre-identified by Caller ID, fast-track!
                if state.get("is_executive") and state.get("verified_user"):
                    exec_name = state["verified_user"]["name"]
                    return {
                        "success": True,
                        "language": language,
                        "is_executive": True,
                        "caller_name": exec_name,
                        "action": "executive_fast_track",
                        "message": f"Caller is executive {exec_name}. Direct transfer to executive queue.",
                    }

                return {"success": True, "language": language, "next_state": state["current_state"]}

            if tool_name == "capture_name":
                employee_name = arguments.get("employee_name", "").strip()
                if not employee_name:
                    return {"success": False, "error": "Name was empty. Ask caller to repeat full name."}

                state["caller_name"] = employee_name
                state["current_state"] = "ask_employee_id"
                return {"success": True, "employee_name": employee_name, "next_state": state["current_state"]}

            if tool_name == "capture_employee_id":
                employee_id = arguments.get("employee_id", "").strip()
                if not employee_id:
                    return {"success": False, "error": "Employee ID was empty. Ask caller to repeat employee ID."}

                state["employee_id"] = employee_id
                state["current_state"] = "verification"
                return {"success": True, "employee_id": employee_id, "next_state": state["current_state"]}

            if tool_name == "verify_user":
                employee_id = arguments.get("employee_id", "").strip() or state.get("employee_id")
                employee_name = arguments.get("employee_name", "").strip() or state.get("caller_name")

                if not employee_name or not employee_id:
                    return {
                        "verified": False,
                        "error": "Both employee name and employee ID are required before verification.",
                    }

                state["caller_name"] = employee_name
                state["employee_id"] = employee_id

                verify_key = f"verify:{state.get('caller_number') or employee_id}"
                lock_remaining = await asyncio.to_thread(is_locked, verify_key)
                if lock_remaining > 0:
                    log_security_event("verification_blocked", verify_key, f"retry_after={lock_remaining}")
                    state["current_state"] = "closing"
                    update_call(state["call_id"], status="verification_blocked")
                    queue_goodbye("verification_failed")
                    return {
                        "verified": False,
                        "action": "call_will_end",
                        "message": "Too many failed verification attempts. Please try again later.",
                    }

                result = await asyncio.to_thread(verify_user, employee_id, employee_name)

                if result.get("verified"):
                    await asyncio.to_thread(reset_rate_limit, verify_key)
                    state["verified_user"] = result
                    state["verification_attempts"] = 0
                    state["is_vip"] = bool(result.get("vip", False))
                    state["is_executive"] = bool(result.get("is_executive", False))
                    state["tier"] = result.get("tier", "STANDARD")
                    state["current_state"] = "verified"

                    update_call(
                        state["call_id"],
                        employee_id=result.get("employee_id"),
                        verified_name=result.get("name"),
                        status="verified",
                        is_vip=1 if state["is_vip"] else 0,
                        tier=state["tier"],
                    )

                    print(f"[VERIFY] Verified: {result.get('name')} ({employee_id}) Tier={state['tier']}")

                    return {
                        "verified": True,
                        "name": result.get("name"),
                        "email": result.get("email"),
                        "employee_id": result.get("employee_id"),
                        "department": result.get("department"),
                        "vip": state["is_vip"],
                        "role": result.get("role"),
                        "tier": state["tier"],
                        "is_executive": state["is_executive"],
                        "next_state": state["current_state"],
                    }

                state["verification_attempts"] += 1
                attempts_left = 3 - state["verification_attempts"]

                await asyncio.to_thread(
                    check_rate_limit, verify_key, VERIFY_FAIL_LIMIT, VERIFY_FAIL_WINDOW, VERIFY_FAIL_LOCK
                )
                log_security_event("verification_failed", verify_key, f"employee_id={employee_id}")

                if state["verification_attempts"] >= 3:
                    state["current_state"] = "closing"
                    update_call(state["call_id"], status="verification_failed")
                    queue_goodbye("verification_failed")
                    return {
                        "verified": False,
                        "attempts_left": 0,
                        "action": "call_will_end",
                        "message": "Maximum verification attempts reached.",
                    }

                state["current_state"] = "ask_name"
                return {
                    "verified": False,
                    "attempts_left": attempts_left,
                    "message": "Name and employee ID did not match.",
                    "next_state": state["current_state"],
                }

            if tool_name == "lookup_knowledge_base":
                topic = arguments.get("topic", "").strip().lower()
                content = KNOWLEDGE_BASE.get(topic)
                if not content:
                    for k, v in KNOWLEDGE_BASE.items():
                        if topic in k or k in topic:
                            content = v
                            break

                if content:
                    return {"found": True, "playbook": content[:1200]}
                return {
                    "found": False,
                    "message": "No specific local playbook found. Use standard IT troubleshooting questions."
                }

            if tool_name == "record_issue_detail":
                issue_category = arguments.get("issue_category", "").strip().lower()
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
                    state["answers_received"].append({"field": field, "value": value})
                    state["troubleshooting_steps"].append(f"{field}: {value}")

                state["question_count"] += 1
                state["current_state"] = "troubleshooting"

                update_call(
                    state["call_id"],
                    status="troubleshooting",
                    summary=state.get("issue_summary") or value,
                )

                return {
                    "success": True,
                    "question_count": state["question_count"],
                    "next_state": state["current_state"],
                }

            if tool_name == "record_resolution":
                title = arguments.get("title", "IT Issue Resolved on Call")
                resolution_summary = arguments.get("resolution_summary", "Issue resolved via AI diagnostics.")

                verified_user = state.get("verified_user") or {}
                customer_email = verified_user.get("email") or f"caller_{state['call_id']}@nationalfinance.com"

                client = get_ticketing_client()
                ticket_body = (
                    f"Caller: {verified_user.get('name', 'Direct Caller')}\n"
                    f"Employee ID: {verified_user.get('employee_id', 'N/A')}\n"
                    f"Department: {verified_user.get('department', 'N/A')}\n\n"
                    f"Resolution Summary:\n{resolution_summary}\n\n"
                    f"Status: Resolved on Call by AI Agent Arif (First-Contact Resolution)"
                )

                result = await asyncio.to_thread(
                    client.create_ticket,
                    customer_email=customer_email,
                    title=title,
                    body=ticket_body,
                    priority="low",
                    category="Resolved on Call",
                    caller_info=verified_user,
                    custom_fields={"call_id": state["call_id"]},
                    status="Resolved",
                )

                ticket_number = result.get("ticket_number")
                update_call(
                    state["call_id"],
                    status="resolved",
                    ai_deflected=1,
                    resolution_type="AI_Resolved",
                    ticket_number=ticket_number,
                    summary=resolution_summary,
                )
                print(f"[TICKET RESOLVED] Auto-ticket {ticket_number} created with status Resolved")

                return {
                    "success": True,
                    "ticket_number": ticket_number,
                    "ticket_number_spoken": digit_by_digit(ticket_number),
                    "message": "Recorded resolution and closed ticket successfully."
                }

            if tool_name == "confirm_ticket":
                confirmed = bool(arguments.get("confirmed"))
                state["ticket_confirmed"] = confirmed
                return {"success": True, "ticket_confirmed": confirmed}

            if tool_name == "create_ticket":
                verified_user = state.get("verified_user") or {}
                customer_email = verified_user.get("email") or f"caller_{state['call_id']}@nationalfinance.com"
                title = arguments.get("title", "IT Support Request")
                description = arguments.get("description", state.get("issue_summary") or "Reported via AI voice agent.")
                priority = arguments.get("priority", "2 normal")
                group = arguments.get("group", "Service Desk")

                if state.get("is_vip") or state.get("tier") in ("P0_EXECUTIVE", "P1_VIP"):
                    priority = "3 high"

                client = get_ticketing_client()

                key_points = "\n".join([f"- {item['field']}: {item['value']}" for item in state["answers_received"][-10:]])
                troubleshooting = "\n".join([f"- {step}" for step in state["troubleshooting_steps"][-10:]])

                ticket_body = (
                    f"Caller: {verified_user.get('name', 'Caller')}\n"
                    f"Employee ID: {verified_user.get('employee_id', 'N/A')}\n"
                    f"Department: {verified_user.get('department', 'N/A')}\n"
                    f"Tier: {state.get('tier', 'STANDARD')}\n\n"
                    f"Issue Summary:\n{description}\n\n"
                    f"Key Points Collected:\n{key_points if key_points else '- None'}\n\n"
                    f"Troubleshooting / Questions:\n{troubleshooting if troubleshooting else '- None'}\n\n"
                    f"Created by: AI Voice Agent Arif"
                )

                try:
                    result = await asyncio.to_thread(
                        client.create_ticket,
                        customer_email=customer_email,
                        title=title,
                        body=ticket_body,
                        priority=priority,
                        category=group,
                        caller_info=verified_user,
                        custom_fields={"call_id": state["call_id"]},
                        status="Open",
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
                            summary=description,
                        )

                        return {
                            "success": True,
                            "ticket_number": ticket_number,
                            "ticket_number_spoken": digit_by_digit(ticket_number),
                            "message": "Ticket created successfully.",
                        }

                    return {"success": False, "error": "Ticketing backend did not return ticket number."}

                except Exception as exc:
                    print(f"[TICKETING ERROR] {exc!r}")
                    update_call(state["call_id"], status="ticket_failed", summary=str(exc))
                    return {"success": False, "error": str(exc)}

            if tool_name == "escalate_emergency":
                reason = arguments.get("reason", "Sev-1 Incident")
                incident_summary = arguments.get("incident_summary", "Critical outage reported.")

                print(f"[EMERGENCY ESCALATION] Reason: {reason}")
                update_call(
                    state["call_id"],
                    status="emergency_escalated",
                    tier="CRITICAL",
                    escalation_reason=reason,
                    summary=incident_summary,
                )

                # 1. Create emergency P1 ticket in Frappe immediately
                verified_user = state.get("verified_user") or {}
                customer_email = verified_user.get("email") or f"emergency_{state['call_id']}@nationalfinance.com"
                client = get_ticketing_client()

                try:
                    await asyncio.to_thread(
                        client.create_ticket,
                        customer_email=customer_email,
                        title=f"🚨 EMERGENCY / OUTAGE: {reason}",
                        body=f"CRITICAL SEV-1 INCIDENT REPORTED VIA VOICE CALL:\n\n{incident_summary}\n\nCaller: {verified_user.get('name', 'Anonymous')}\nPhone: {state.get('caller_number')}",
                        priority="urgent",
                        category="Critical Incident",
                        caller_info=verified_user,
                        custom_fields={"call_id": state["call_id"]},
                        status="Open",
                    )
                except Exception as e:
                    print(f"[EMERGENCY TICKET ERROR] {e}")

                # 2. Redirect live call to Emergency Queue (7003)
                channel = state.get("asterisk_channel")
                if channel:
                    transfer_res = await asyncio.to_thread(
                        transfer_call,
                        channel,
                        queue_type="emergency",
                        caller_context={
                            "caller_name": verified_user.get("name", "Emergency Caller"),
                            "tier": "CRITICAL_EMERGENCY",
                            "reason": reason,
                        }
                    )
                    if transfer_res.get("success"):
                        state["call_ending"] = True
                        update_call(state["call_id"], transferred=1, transfer_target=ASTERISK_QUEUE_EMERGENCY)
                        return {"success": True, "escalated": True, "target": ASTERISK_QUEUE_EMERGENCY}

                return {"success": True, "escalated": True, "target": ASTERISK_QUEUE_EMERGENCY}

            if tool_name == "transfer_to_agent":
                reason = arguments.get("reason", "caller_requested_human_agent")
                queue_type = arguments.get("queue_type", "standard")

                if state.get("is_executive") or state.get("tier") == "P0_EXECUTIVE":
                    queue_type = "executive"

                channel = state.get("asterisk_channel")
                if not channel:
                    return {"success": False, "error": "Asterisk channel not available for transfer."}

                target_extension = ASTERISK_QUEUE_EXECUTIVE if queue_type == "executive" else ASTERISK_QUEUE_STANDARD
                print(f"[TRANSFER] Transferring {channel} to {queue_type} ({target_extension}). Reason={reason}")

                verified_user = state.get("verified_user") or {}
                result = await asyncio.to_thread(
                    transfer_call,
                    channel,
                    queue_type=queue_type,
                    caller_context={
                        "caller_name": verified_user.get("name", "Unknown"),
                        "employee_id": verified_user.get("employee_id", "N/A"),
                        "tier": state.get("tier", "STANDARD"),
                        "reason": reason,
                    }
                )

                if result.get("success"):
                    update_call(
                        state["call_id"],
                        transferred=1,
                        transfer_target=result.get("extension"),
                        status="transferred",
                        escalation_reason=reason,
                    )
                    state["call_ending"] = True
                    return {"success": True, "message": f"Transferred to {queue_type} queue."}

                update_call(state["call_id"], status="transfer_failed", summary=result.get("error", "Transfer failed"))
                return {"success": False, "error": result.get("error", "Transfer failed")}

            if tool_name == "report_audio_issue":
                state["background_noise_warning_count"] += 1
                if state["background_noise_warning_count"] >= 3:
                    queue_goodbye("audio_unclear")
                    return {"success": True, "action": "call_will_end"}
                return {"success": True, "warning_count": state["background_noise_warning_count"]}

            if tool_name == "close_call":
                reason = arguments.get("reason", "caller_requested")
                queue_goodbye(reason)
                return {"closed": True, "reason": reason}

            return {"success": False, "error": f"Unknown tool: {tool_name}"}

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
            result = {"success": False, "error": f"Tool {tool_name} failed: {str(exc)}"}

        await openai_ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result),
                    },
                }
            )
        )

        prefix = language_prefix(state)

        if tool_name == "set_language":
            if result.get("action") == "executive_fast_track":
                queue_response(
                    f"{prefix} Welcome Mr. {result.get('caller_name')} respectfully. "
                    f"Say: I am transferring you directly to our Senior Executive Support Desk right now. "
                    f"Then call transfer_to_agent with queue_type='executive' immediately."
                )
            elif state["language"] == "ar":
                queue_response("Respond only in Arabic. Confirm Arabic briefly and ask for the caller's full name.")
            else:
                queue_response("Respond only in English. Confirm English briefly and ask for the caller's full name.")

        elif tool_name == "capture_name" and result.get("success"):
            queue_response(f"{prefix} Ask for the caller's employee ID. Keep it short.")

        elif tool_name == "capture_employee_id" and result.get("success"):
            queue_response(f"{prefix} Say please wait while I verify your details, then call verify_user immediately.")

        elif tool_name == "verify_user" and result.get("verified"):
            if result.get("is_executive"):
                queue_response(
                    f"{prefix} Greet Mr. {result.get('name')} with executive priority. "
                    f"Say: Connecting you to our Priority Executive Desk immediately. Then call transfer_to_agent with queue_type='executive'."
                )
            elif result.get("vip"):
                queue_response(
                    f"{prefix} Say the caller is verified and marked for priority support. "
                    f"Ask: How can I assist you today?"
                )
            else:
                queue_response(f"{prefix} Say the caller is verified. Then ask: How can I help you today?")

        elif tool_name == "verify_user" and not result.get("verified"):
            if result.get("attempts_left", 0) > 0:
                queue_response(
                    f"{prefix} Say the details did not match. Ask for full name and employee ID again."
                )

        elif tool_name == "lookup_knowledge_base" and result.get("found"):
            queue_response(
                f"{prefix} Based on the playbook, give the caller the single most practical next step. Ask if it helps."
            )

        elif tool_name == "record_resolution" and result.get("success"):
            ticket_spoken = result.get("ticket_number_spoken")
            if state["language"] == "ar":
                queue_response(
                    f"Respond only in Arabic. Say that the issue was marked as resolved under reference ticket {ticket_spoken}. Ask if they need anything else."
                )
            else:
                queue_response(
                    f"Respond only in English. Say: Excellent, I have logged this as resolved with reference ticket {ticket_spoken}. Is there anything else I can help you with?"
                )

        elif tool_name == "create_ticket" and result.get("success"):
            ticket_spoken = result.get("ticket_number_spoken")
            if state["language"] == "ar":
                queue_response(
                    f"Respond only in Arabic. Say the ticket was created successfully. رقم التذكرة هو {ticket_spoken}. Ask if they need anything else."
                )
            else:
                queue_response(
                    f"Respond only in English. Say: Your ticket has been created successfully. Your ticket number is {ticket_spoken}. Is there anything else I can help you with?"
                )

        elif tool_name == "escalate_emergency":
            if state["language"] == "ar":
                queue_response(
                    "Respond only in Arabic. Say: تم تصنيف الحالة كطارئة. أحولك مباشرة إلى فريق الطوارئ والمهندسين المناوبين."
                )
            else:
                queue_response(
                    "Respond only in English. Say: This has been flagged as a critical incident. Transferring you immediately to the on-call emergency team."
                )

        await send_queued_response_if_any()

    async def asterisk_to_openai():
        try:
            async for message in asterisk_ws:
                if state["call_ending"]:
                    break

                if time.monotonic() - state["started_monotonic"] > CALL_MAX_SECONDS:
                    queue_goodbye("timeout")
                    await send_queued_response_if_any()
                    continue

                # Full-duplex: only skip if call is completely closing/ending
                if state["closing"]:
                    continue

                if isinstance(message, bytes):
                    await openai_ws.send(
                        json.dumps(
                            {
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(message).decode("utf-8"),
                            }
                        )
                    )
                else:
                    if isinstance(message, str) and "MEDIA_START" in message:
                        parts = message.split()
                        for part in parts:
                            if part.startswith("channel:"):
                                state["asterisk_channel"] = part.replace("channel:", "").strip()
                            if part.startswith("caller:"):
                                caller_num = part.replace("caller:", "").strip()
                                if caller_num:
                                    state["caller_number"] = caller_num
                                    update_call(state["call_id"], caller_number=caller_num)

                                    # Fast-track check for CEO, CFO, C-Suite
                                    pre_user = lookup_caller_by_phone(caller_num)
                                    if pre_user and pre_user.get("is_executive"):
                                        state["is_executive"] = True
                                        state["is_vip"] = True
                                        state["tier"] = "P0_EXECUTIVE"
                                        state["verified_user"] = pre_user
                                        state["caller_name"] = pre_user["name"]
                                        state["employee_id"] = pre_user["employee_id"]
                                        update_call(
                                            state["call_id"],
                                            is_vip=1,
                                            tier="P0_EXECUTIVE",
                                            verified_name=pre_user["name"],
                                            employee_id=pre_user["employee_id"],
                                        )
                                        print(f"[EXECUTIVE DETECTED] CLI match for {pre_user['name']} ({pre_user['role']})")

                            if part.startswith("channel_id:"):
                                real_call_id = part.replace("channel_id:", "").strip()
                                if real_call_id:
                                    old_call_id = state["call_id"]
                                    state["call_id"] = real_call_id
                                    rename_call_id(old_call_id, real_call_id)
                                    update_call(state["call_id"], status="in_progress")

                        if state["caller_number"]:
                            rl = check_rate_limit(
                                f"call:{state['caller_number']}",
                                CALLS_PER_NUMBER_LIMIT,
                                CALLS_PER_NUMBER_WINDOW,
                                CALLS_PER_NUMBER_LOCK,
                            )
                            if not rl["allowed"]:
                                log_security_event(
                                    "call_rate_limited",
                                    state["caller_number"],
                                    f"reason={rl['reason']} retry_after={rl['retry_after']}",
                                )
                                update_call(state["call_id"], status="rejected")
                                state["call_ending"] = True
                                await asterisk_ws.close()
                                return

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

                elif event_type == "input_audio_buffer.speech_started":
                    # Caller interrupted while AI is speaking (barge-in)
                    state["active_response"] = False

                elif event_type == "response.output_audio.delta":
                    if state["call_ending"]:
                        break
                    audio_b64 = event.get("delta", "")
                    if audio_b64:
                        raw_pcm = base64.b64decode(audio_b64)
                        # Chunk into 320-byte (40ms @ 8kHz PCMU) frames to prevent jitter buffer underrun/overflow
                        chunk_size = 320
                        for i in range(0, len(raw_pcm), chunk_size):
                            await asterisk_ws.send(raw_pcm[i:i + chunk_size])

                elif event_type == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        await handle_tool_call(event)

                elif event_type == "response.done":
                    response = event.get("response", {})
                    status = response.get("status")
                    state["active_response"] = False

                    if status == "failed":
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
                    transcript = (event.get("transcript") or "").strip()
                    if transcript:
                        print(f"[CALLER SAID] {transcript}")

                elif event_type == "error":
                    print(f"[OPENAI ERROR] {json.dumps(event, indent=2)}")
                    error = event.get("error", {})
                    if error.get("code") == "conversation_already_has_active_response":
                        state["active_response"] = True

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            print(f"[OPENAI->ASTERISK] {exc!r}")

    tasks = [
        asyncio.create_task(asterisk_to_openai()),
        asyncio.create_task(openai_to_asterisk()),
    ]

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    try:
        await openai_ws.close()
    except Exception:
        pass

    if not state["call_ending"] and state.get("current_state") != "transferred":
        log_close_call(state["call_id"], status="ended")


async def main():
    validate_bridge_config()
    reconcile_stale_calls(CALL_MAX_SECONDS)

    print(f"[SERVER] Starting on {ASTERISK_WS_HOST}:{ASTERISK_WS_PORT}")
    print(f"[SERVER] Model: {OPENAI_REALTIME_MODEL}")
    print(f"[SERVER] Ticketing Provider: {os.getenv('TICKETING_SYSTEM', 'frappe').upper()}")
    print(f"[SERVER] Loaded {len(KNOWLEDGE_BASE)} Knowledge Base Playbooks: {list(KNOWLEDGE_BASE.keys())}")

    async with websockets.serve(
        handle_asterisk_call,
        ASTERISK_WS_HOST,
        ASTERISK_WS_PORT,
        max_size=None,
        ping_interval=20,
        ping_timeout=10,
    ):
        print("[SERVER] Ready. Waiting for calls...")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SERVER] Stopped")
