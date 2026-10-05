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

# Multi-queue targets (Webex Calling: NF L1 IT Support -> 919, L2 IT Support VIP -> 920)
QUEUE_STANDARD = os.getenv("ASTERISK_QUEUE_STANDARD", os.getenv("ASTERISK_AGENT_EXTENSION", "919"))
QUEUE_EXECUTIVE = os.getenv("ASTERISK_QUEUE_EXECUTIVE", "920")
QUEUE_EMERGENCY = os.getenv("ASTERISK_QUEUE_EMERGENCY", "920")

# Multi-queue names (Asterisk queues.conf)
QUEUE_NAME_STANDARD = os.getenv("ASTERISK_QUEUE_NAME_STANDARD", "it-support")
QUEUE_NAME_EXECUTIVE = os.getenv("ASTERISK_QUEUE_NAME_EXECUTIVE", "it-vip-exec")
QUEUE_NAME_EMERGENCY = os.getenv("ASTERISK_QUEUE_NAME_EMERGENCY", "it-emergency")

ENFORCE_QUEUE_CAPACITY = os.getenv("ENFORCE_QUEUE_CAPACITY", "true").lower() in ("1", "true", "yes")


def _ami_send(sock, action):
    data = ""
    for key, value in action.items():
        data += f"{key}: {value}\r\n"
    data += "\r\n"
    sock.sendall(data.encode())


def _ami_connect():
    """Establishes an authenticated socket connection to Asterisk AMI."""
    raw_sock = socket.create_connection((ASTERISK_AMI_HOST, ASTERISK_AMI_PORT), timeout=5)
    if ASTERISK_AMI_TLS:
        import ssl
        ssl_ctx = ssl.create_default_context()
        if not ASTERISK_AMI_TLS_VERIFY:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        sock = ssl_ctx.wrap_socket(raw_sock, server_hostname=ASTERISK_AMI_HOST)
    else:
        sock = raw_sock

    sock.recv(1024)  # Asterisk Call Manager banner

    _ami_send(sock, {
        "Action": "Login",
        "Username": ASTERISK_AMI_USER,
        "Secret": ASTERISK_AMI_SECRET
    })

    login_resp = sock.recv(4096).decode(errors="ignore")
    if "Success" not in login_resp:
        sock.close()
        raise RuntimeError(f"AMI login failed: {login_resp.strip()}")

    return sock


def resolve_queue_target(queue_type: str = "standard", extension: str = None) -> str:
    if extension:
        return extension

    queue_type = (queue_type or "standard").strip().lower()
    if queue_type in ("executive", "ceo", "cfo", "p0_executive", "vip", "p1_vip", "l2"):
        return QUEUE_EXECUTIVE
    elif queue_type in ("emergency", "critical", "p1", "outage"):
        return QUEUE_EMERGENCY
    return QUEUE_STANDARD


def resolve_queue_name(queue_type: str = "standard") -> str:
    queue_type = (queue_type or "standard").strip().lower()
    if queue_type in ("executive", "ceo", "cfo", "p0_executive", "vip", "p1_vip", "l2", QUEUE_EXECUTIVE, "920", "7002"):
        return QUEUE_NAME_EXECUTIVE
    elif queue_type in ("emergency", "critical", "p1", "outage", QUEUE_EMERGENCY, "7003"):
        return QUEUE_NAME_EMERGENCY
    return QUEUE_NAME_STANDARD


def check_queue_availability(queue_type: str = "standard", extension: str = None) -> dict:
    """
    Pre-flight check verifying whether human agents are logged in and available
    before transferring a live caller to an Asterisk queue.
    Prevents endless music-on-hold loops and abandoned calls.
    """
    if not ENFORCE_QUEUE_CAPACITY:
        return {"available": True, "reason": "capacity_enforcement_disabled"}

    if not ASTERISK_AMI_USER or not ASTERISK_AMI_SECRET:
        return {"available": True, "reason": "ami_not_configured_fallback"}

    # Emergency queues always allow transfer / fail open
    q_lower = (queue_type or "").strip().lower()
    if q_lower in ("emergency", "critical", "p1", "outage", QUEUE_EMERGENCY):
        return {"available": True, "queue_name": QUEUE_NAME_EMERGENCY, "emergency": True}

    queue_name = resolve_queue_name(queue_type)

    try:
        sock = _ami_connect()
        try:
            _ami_send(sock, {
                "Action": "QueueSummary",
                "Queue": queue_name,
                "ActionID": f"qcheck_{int(os.getpid())}"
            })

            # Read AMI response buffer (may contain multiple event packets)
            raw_data = ""
            sock.settimeout(3.0)
            while True:
                try:
                    chunk = sock.recv(4096).decode(errors="ignore")
                    if not chunk:
                        break
                    raw_data += chunk
                    if "QueueSummaryComplete" in raw_data or "NoSuchQueue" in raw_data or len(raw_data) > 8192:
                        break
                except socket.timeout:
                    break

            _ami_send(sock, {"Action": "Logoff"})

            # Parse key-values from QueueSummary event
            logged_in = 0
            available_agents = 0
            callers_waiting = 0
            found_summary = False

            for line in raw_data.splitlines():
                line = line.strip()
                if line.startswith("LoggedIn:"):
                    logged_in = int(line.split(":", 1)[1].strip() or 0)
                    found_summary = True
                elif line.startswith("Available:"):
                    available_agents = int(line.split(":", 1)[1].strip() or 0)
                    found_summary = True
                elif line.startswith("Callers:"):
                    callers_waiting = int(line.split(":", 1)[1].strip() or 0)

            if not found_summary:
                # If queue is not found or app_queue not tracking summary, fail open gracefully
                return {
                    "available": True,
                    "queue_name": queue_name,
                    "reason": "queue_summary_not_tracked_fallback",
                    "raw": raw_data[:200]
                }

            is_available = available_agents > 0 or (logged_in > 0 and callers_waiting < 5)

            return {
                "available": is_available,
                "queue_name": queue_name,
                "logged_in": logged_in,
                "available_agents": available_agents,
                "callers_waiting": callers_waiting,
                "reason": "agents_available" if is_available else "no_agents_available"
            }

        finally:
            try:
                sock.close()
            except Exception:
                pass

    except Exception as exc:
        # On connection failure to AMI, fail open so calls can still transfer if PBX is functional
        return {
            "available": True,
            "queue_name": queue_name,
            "error": str(exc),
            "reason": "ami_error_fail_open"
        }


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
        sock = _ami_connect()
        try:
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
        finally:
            try:
                sock.close()
            except Exception:
                pass

    except Exception as exc:
        return {
            "success": False,
            "error": str(exc)
        }


def get_active_channel_info(call_uuid=None):
    """Queries Asterisk AMI Status to find the active incoming channel and caller ID."""
    if not ASTERISK_AMI_USER or not ASTERISK_AMI_SECRET:
        return None
    try:
        sock = _ami_connect()
        try:
            _ami_send(sock, {"Action": "Status"})
            data = ""
            while "StatusComplete" not in data:
                chunk = sock.recv(4096).decode(errors="ignore")
                if not chunk:
                    break
                data += chunk
            _ami_send(sock, {"Action": "Logoff"})

            events = data.split("\r\n\r\n")
            first_pjsip = None
            matched = None
            for ev in events:
                if "Event: Status" in ev:
                    lines = ev.split("\r\n")
                    ev_dict = {}
                    for line in lines:
                        if ": " in line:
                            k, v = line.split(": ", 1)
                            ev_dict[k.strip()] = v.strip()
                    chan = ev_dict.get("Channel")
                    caller_num = ev_dict.get("CallerIDNum")
                    acct = ev_dict.get("AccountCode")
                    if chan and ("PJSIP" in chan or "Local" in chan or "SIP" in chan):
                        if not first_pjsip:
                            first_pjsip = {"channel": chan, "caller_num": caller_num}
                        if call_uuid and acct and acct.strip() == str(call_uuid).strip():
                            matched = {"channel": chan, "caller_num": caller_num}
                            break
            return matched or first_pjsip
        finally:
            sock.close()
    except Exception:
        pass
    return None


def hangup_channel(channel, cause="16"):
    """Hangs up an active Asterisk channel via AMI."""
    if not channel or not ASTERISK_AMI_USER or not ASTERISK_AMI_SECRET:
        return False
    try:
        sock = _ami_connect()
        try:
            _ami_send(sock, {
                "Action": "Hangup",
                "Channel": channel,
                "Cause": str(cause),
            })
            _ami_send(sock, {"Action": "Logoff"})
            return True
        finally:
            sock.close()
    except Exception:
        return False
