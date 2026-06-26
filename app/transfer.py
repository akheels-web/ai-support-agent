import os
import socket
from dotenv import load_dotenv

load_dotenv("/opt/ai-support-agent/.env", override=True)

ASTERISK_AMI_HOST = os.getenv("ASTERISK_AMI_HOST", "127.0.0.1")
ASTERISK_AMI_PORT = int(os.getenv("ASTERISK_AMI_PORT", "5038"))
ASTERISK_AMI_USER = os.getenv("ASTERISK_AMI_USER", "")
ASTERISK_AMI_SECRET = os.getenv("ASTERISK_AMI_SECRET", "")

TRANSFER_CONTEXT = os.getenv("ASTERISK_TRANSFER_CONTEXT", "from-internal")
TRANSFER_EXTENSION = os.getenv("ASTERISK_AGENT_EXTENSION", "7001")
TRANSFER_PRIORITY = os.getenv("ASTERISK_TRANSFER_PRIORITY", "1")


def _ami_send(sock, action):
    data = ""

    for key, value in action.items():
        data += f"{key}: {value}\r\n"

    data += "\r\n"
    sock.sendall(data.encode())


def transfer_call(channel, extension=None, context=None):
    """
    Transfer live Asterisk channel to configured human support extension/queue.

    Requires AMI enabled in Asterisk manager.conf.
    """

    if not channel:
        return {
            "success": False,
            "error": "Asterisk channel was empty. Cannot transfer."
        }

    if not ASTERISK_AMI_USER or not ASTERISK_AMI_SECRET:
        return {
            "success": False,
            "error": "AMI credentials missing. Set ASTERISK_AMI_USER and ASTERISK_AMI_SECRET."
        }

    extension = extension or TRANSFER_EXTENSION
    context = context or TRANSFER_CONTEXT

    try:
        with socket.create_connection((ASTERISK_AMI_HOST, ASTERISK_AMI_PORT), timeout=5) as sock:
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

            _ami_send(sock, {
                "Action": "Redirect",
                "Channel": channel,
                "Context": context,
                "Exten": extension,
                "Priority": TRANSFER_PRIORITY
            })

            response = sock.recv(4096).decode(errors="ignore")

            _ami_send(sock, {"Action": "Logoff"})

            if "Success" in response:
                return {
                    "success": True,
                    "extension": extension,
                    "context": context,
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