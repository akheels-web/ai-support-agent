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

START OF CALL:
- First say only: "Hi, I am Arif from National Finance IT Support team. Please say Arabic or English to continue."
- Do not ask for issue before language is selected.
- If caller says hello, ask again: "Please say Arabic or English to continue."

LANGUAGE:
- If caller chooses English, continue only in English.
- If caller chooses Arabic, continue only in Arabic.
- Do not mix languages.
- Ticket number and ticket status must be spoken in the selected language.

VERIFICATION:
- After language selection, ask for caller name.
- Then ask for employee ID.
- You must call verify_user with the employee ID.
- Do not say caller is verified unless verify_user returns verified=true.
- Do not create a verified ticket unless caller is verified.

MANDATORY TOOL RULES:
- Never say "I am creating a ticket" unless you call create_ticket immediately.
- Never say "ticket created" unless create_ticket returns success=true.
- Never invent ticket numbers.
- If create_ticket returns ticket_number, read it clearly to the caller.
- In English say: "Your ticket number is ..."
- In Arabic say: "رقم التذكرة هو ..."
- After reading ticket number, ask if the caller needs anything else.

VPN SUPPORT:
- If caller reports VPN issue, ask for the exact error.
- If caller says TLS error, ask whether it appears before or after login.
- Then create a ticket for Network Support.
- Do not go silent after saying you will create a ticket.
- Use create_ticket and wait for the real ticket number.

IT SUPPORT QUALITY:
- Do not give random advice.
- Do not suggest restarting for account lockout as main solution.
- For account lockout, password, MFA, access, or security issue, verify user first and create/escalate a ticket.
- Ask short clarifying questions.
- If unsure, create a ticket instead of guessing.

SILENCE:
- If backend tool is running, say: "Please wait while I create the ticket."
- If no tool result is received, do not keep silent. Say there is a technical issue creating the ticket.

ENDING:
- After ticket number is provided, ask if caller needs anything else.
- If caller says no, say goodbye.
- Keep responses short and phone-friendly.
"""


async def connect_openai():
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }

    ws = await websockets.connect(
        OPENAI_WS_URL,
        additional_headers=headers,
        max_size=None,
    )

    session_update = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": SYSTEM_PROMPT,
            "output_modalities": ["audio"],
            "tools": [
                {
                    "type": "function",
                    "name": "verify_user",
                    "description": "Verify caller using employee ID from users.csv.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "employee_id": {
                                "type": "string",
                                "description": "Employee ID provided by caller"
                            }
                        },
                        "required": ["employee_id"]
                    }
                },
                {
                    "type": "function",
                    "name": "create_ticket",
                    "description": "Create a support ticket in Zammad and return ticket number.",
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
                                "type": "string"
                            }
                        },
                        "required": [
                            "customer_email",
                            "title",
                            "description",
                            "priority",
                            "group"
                        ]
                    }
                }
            ],
            "tool_choice": "auto",
            "audio": {
                "input": {
                    "format": {
                        "type": "audio/pcmu"
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.45,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
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

    await ws.send(json.dumps(session_update))

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
    verified_user = None
    last_ticket_number = None
    call_should_end = False
    ticket_announcement_requested = False
    ticket_announcement_started = False

    print("[ASTERISK] New call connected to AI media websocket")

    try:
        openai_ws = await connect_openai()
        print("[OPENAI] Connected to OpenAI Realtime")
    except Exception as e:
        print(f"[OPENAI] Connection failed: {repr(e)}")
        await asterisk_ws.close()
        return

    async def handle_tool_call(event):
        nonlocal verified_user
        nonlocal last_ticket_number
        nonlocal call_should_end
        nonlocal ticket_announcement_requested

        item = event.get("item", {})
        tool_name = item.get("name")
        call_id = item.get("call_id")
        arguments_raw = item.get("arguments", "{}")

        try:
            arguments = json.loads(arguments_raw)
        except Exception:
            arguments = {}

        print(f"[TOOL] Tool requested: {tool_name}")
        print(f"[TOOL] Arguments: {arguments}")

        if tool_name == "verify_user":
            from app.verify import verify_user

            employee_id = arguments.get("employee_id", "")
            result = verify_user(employee_id)

            if result.get("verified"):
                verified_user = result

            tool_result = result

        elif tool_name == "create_ticket":
            from app.zammad_api import create_ticket

            if not verified_user:
                tool_result = {
                    "success": False,
                    "error": "Caller is not verified. Ticket cannot be created as verified user."
                }
            else:
                customer_email = verified_user.get("email") or arguments.get("customer_email")
                title = arguments.get("title", "IT Support Request")
                description = arguments.get("description", "Issue reported through AI voice agent.")
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

                    last_ticket_number = result.get("ticket_number")
                    call_should_end = True
                    ticket_announcement_requested = True

                    tool_result = {
                        "success": True,
                        "ticket_number": last_ticket_number,
                        "message": "Ticket created successfully"
                    }

                    print(f"[ZAMMAD] Ticket created: {last_ticket_number}")

                except Exception as e:
                    tool_result = {
                        "success": False,
                        "error": str(e)
                    }

                    print(f"[ZAMMAD] Ticket creation failed: {repr(e)}")

        else:
            tool_result = {
                "success": False,
                "error": f"Unknown tool: {tool_name}"
            }

        await openai_ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(tool_result)
            }
        }))

        await openai_ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "output_modalities": ["audio"],
                "instructions": (
                    "Continue the call based on the tool result. "
                    "If verification succeeded, tell the caller they are verified and continue. "
                    "If verification failed, politely say verification failed. "
                    "If a ticket was created, clearly read the ticket number to the caller. "
                    "Then ask if the caller needs anything else."
                )
            }
        }))

    async def asterisk_to_openai():
        try:
            async for message in asterisk_ws:
                if isinstance(message, bytes):
                    audio_b64 = base64.b64encode(message).decode("utf-8")

                    event = {
                        "type": "input_audio_buffer.append",
                        "audio": audio_b64,
                    }

                    await openai_ws.send(json.dumps(event))

                else:
                    print(f"[ASTERISK CONTROL] {message}")

        except Exception as e:
            print(f"[ASTERISK->OPENAI] Error: {e}")

    async def openai_to_asterisk():
        nonlocal ticket_announcement_started

        try:
            async for raw in openai_ws:
                event = json.loads(raw)
                event_type = event.get("type")

                if event_type == "response.output_audio.delta":
                    audio_b64 = event.get("delta")

                    if audio_b64:
                        audio_bytes = base64.b64decode(audio_b64)
                        await asterisk_ws.send(audio_bytes)

                        if ticket_announcement_requested and last_ticket_number:
                            ticket_announcement_started = True

                elif event_type == "response.output_text.delta":
                    print(event.get("delta", ""), end="", flush=True)

                elif event_type == "response.output_item.done":
                    item = event.get("item", {})

                    if item.get("type") == "function_call":
                        await handle_tool_call(event)

                elif event_type == "response.done":
                    print("\n[OPENAI] Response completed")

                    if call_should_end and last_ticket_number and ticket_announcement_started:
                        print(f"[CALL] Ticket {last_ticket_number} was announced. Ending call shortly.")
                        await asyncio.sleep(4)
                        await asterisk_ws.close()
                        return

                elif event_type == "input_audio_buffer.speech_started":
                    print("[OPENAI] Caller started speaking")

                elif event_type == "input_audio_buffer.speech_stopped":
                    print("[OPENAI] Caller stopped speaking")

                elif event_type == "error":
                    print(f"[OPENAI ERROR] {json.dumps(event, indent=2)}")

                else:
                    if event_type:
                        print(f"[OPENAI EVENT] {event_type}")

        except Exception as e:
            print(f"[OPENAI->ASTERISK] Error: {e}")

    await asyncio.gather(
        asterisk_to_openai(),
        openai_to_asterisk(),
    )


async def main():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing in /opt/ai-support-agent/.env")

    print(f"[SERVER] Starting Asterisk media WebSocket server on {ASTERISK_WS_HOST}:{ASTERISK_WS_PORT}")
    print(f"[SERVER] OpenAI model: {OPENAI_REALTIME_MODEL}")

    async with websockets.serve(
        handle_asterisk_call,
        ASTERISK_WS_HOST,
        ASTERISK_WS_PORT,
        max_size=None,
    ):
        print("[SERVER] Ready. Waiting for Asterisk calls...")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped")
