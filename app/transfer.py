import os
import socket
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = os.getenv("ENV_FILE", str(BASE_DIR / ".env"))
load_dotenv(env_path, override=True)

ASTERISK_AMI_HOST = os.getenv("ASTERISK_AMI_HOST", "127.0.0.1")
ASTERISK_AMI_PORT = int(os.getenv("ASTERISK_AMI_PORT", "5038"))
ASTERISK_AMI_USER = os.getenv("ASTERISK_AMI_USER", "")
ASTERISK_AMI_SECRET = os.getenv("ASTERISK_AMI_SECRET", "")
ASTERISK_AMI_TLS = os.getenv("ASTERISK_AMI_TLS", "false").lower() in ("1", "true", "yes")
ASTERISK_AMI_TLS_VERIFY = os.getenv("ASTERISK_AMI_TLS_VERIFY", "true").lower() in ("1", "true", "yes")

TRANSFER_CONTEXT = os.getenv("ASTERISK_TRANSFER_CONTEXT", "from-internal")
TRANSFER_PRIORITY = os.getenv("ASTERISK_TRANSFER_PRIORITY", "1")

# Multi-queue targets
QUEUE_STANDARD = os.getenv("ASTERISK_QUEUE_STANDARD", os.getenv("ASTERISK_AGENT_EXTENSION", "7001"))
QUEUE_EXECUTIVE = os.getenv("ASTERISK_QUEUE_EXECUTIVE", "7002")
QUEUE_EMERGENCY = os.getenv("ASTERISK_QUEUE_EMERGENCY", "7003")


def _ami_send(sock, action):
    data = ""
    for key, value in action.items():
        data += f"{key}: {value}\r\n"
    data += "\r\n"
    sock.sendall(data.encode())


def resolve_queue_target(queue_type: str = "standard", extension: str = None) -> str:
    if extension:
        return extension

    queue_type = (queue_type or "standard").strip().lower()
    if queue_type in ("executive", "ceo", "cfo", "p0_executive"):
        return QUEUE_EXECUTIVE
    elif queue_type in ("emergency", "critical", "p1", "outage"):
        return QUEUE_EMERGENCY
    return QUEUE_STANDARD


def transfer_call(channel, queue_type="standard", extension=None, context=None, caller_context=None):
    """
    Transfer live Asterisk channel to human support queue (Standard, Executive, or Emergency).
    Passes contextual variables (Caller Name, Tier, Ticket Number) to Asterisk channel before redirect.
    """
    if not channel:
        return {
            "success": False,
            "error": "Telephony line was empty. Cannot transfer."
        }

    if not ASTERISK_AMI_USER or not ASTERISK_AMI_SECRET:
        return {
            "success": False,
            "error": "Telephony control credentials not configured."
        }

    target_extension = resolve_queue_target(queue_type, extension)
    target_context = context or TRANSFER_CONTEXT

    try:
        with socket.create_connection((ASTERISK_AMI_HOST, ASTERISK_AMI_PORT), timeout=5) as raw_sock:
            if ASTERISK_AMI_TLS:
                import ssl
                ssl_ctx = ssl.create_default_context()
                if not ASTERISK_AMI_TLS_VERIFY:
                    ssl_ctx.check_hostname = False
                    ssl_ctx.verify_mode = ssl.CERT_NONE
                sock = ssl_ctx.wrap_socket(raw_sock, server_hostname=ASTERISK_AMI_HOST)
            else:
                sock = raw_sock

            sock.recv(1024)

            _ami_send(sock, {
                "Action": "Login",
                "Username": ASTERISK_AMI_USER,
                "Secret": ASTERISK_AMI_SECRET
            })

            login_response = sock.recv(4096).decode(errors="ignore")
            if "Success" not in login_response:
                return {
                    "success": False,
                    "error": f"AMI login failed: {login_response}"
                }

            # Inject caller context variables for screen-pop / softphone display
            if caller_context and isinstance(caller_context, dict):
                for var_name, var_value in caller_context.items():
                    if var_value:
                        safe_name = f"AI_{var_name.upper()}"
                        safe_val = str(var_value).replace("\r", " ").replace("\n", " ")
                        _ami_send(sock, {
                            "Action": "Setvar",
                            "Channel": channel,
                            "Variable": safe_name,
                            "Value": safe_val
                        })
                        sock.recv(1024)

            # Redirect live channel to queue extension
            _ami_send(sock, {
                "Action": "Redirect",
                "Channel": channel,
                "Context": target_context,
                "Exten": target_extension,
                "Priority": TRANSFER_PRIORITY
            })

            response = sock.recv(4096).decode(errors="ignore")
            _ami_send(sock, {"Action": "Logoff"})

            if "Success" in response:
                return {
                    "success": True,
                    "extension": target_extension,
                    "queue_type": queue_type,
                    "context": target_context,
                    "response": response
                }

            return {
                "success": False,
                "error": response
            }

    except Exception as exc:
        return {
            "success": False,
            "error": str(exc)
        }