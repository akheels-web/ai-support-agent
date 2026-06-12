import requests
from app.config import ZAMMAD_URL, ZAMMAD_TOKEN

HEADERS = {
    "Authorization": f"Token token={ZAMMAD_TOKEN}",
    "Content-Type": "application/json"
}


def create_ticket(customer_email, title, body, group="Service Desk", priority="2 normal"):
    payload = {
        "title": title,
        "group": group,
        "customer": customer_email,
        "priority": priority,
        "article": {
            "subject": title,
            "body": body,
            "type": "note",
            "internal": False
        }
    }

    response = requests.post(
        f"{ZAMMAD_URL}/api/v1/tickets",
        headers=HEADERS,
        json=payload,
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    return {
        "success": True,
        "ticket_id": data.get("id"),
        "ticket_number": data.get("number"),
        "raw": data
    }


def update_ticket(ticket_id, note):
    payload = {
        "article": {
            "subject": "Update from AI voice agent",
            "body": note,
            "type": "note",
            "internal": False
        }
    }

    response = requests.put(
        f"{ZAMMAD_URL}/api/v1/tickets/{ticket_id}",
        headers=HEADERS,
        json=payload,
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    return {
        "success": True,
        "ticket_id": data.get("id"),
        "ticket_number": data.get("number"),
        "raw": data
    }


def get_ticket(ticket_id):
    response = requests.get(
        f"{ZAMMAD_URL}/api/v1/tickets/{ticket_id}",
        headers=HEADERS,
        timeout=20
    )

    response.raise_for_status()
    return response.json()
