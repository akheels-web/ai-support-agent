import requests
from app.config import ZAMMAD_URL, ZAMMAD_TOKEN

HEADERS = {
    "Authorization": f"Token token={ZAMMAD_TOKEN}",
    "Content-Type": "application/json"
}


def _raise_with_body(response):
    if response.status_code >= 400:
        print("[ZAMMAD ERROR STATUS]", response.status_code)
        print("[ZAMMAD ERROR BODY]", response.text)
        response.raise_for_status()


def find_user_by_email(email):
    response = requests.get(
        f"{ZAMMAD_URL}/api/v1/users/search",
        headers=HEADERS,
        params={"query": email},
        timeout=20
    )

    _raise_with_body(response)
    data = response.json()

    if isinstance(data, list):
        for user in data:
            if str(user.get("email", "")).lower() == str(email).lower():
                return user

    return None


def create_customer_if_missing(email, firstname="AI", lastname="Caller"):
    existing_user = find_user_by_email(email)

    if existing_user:
        print(f"[ZAMMAD] Customer exists: {email}")
        return existing_user

    payload = {
        "firstname": firstname,
        "lastname": lastname,
        "email": email,
        "roles": ["Customer"],
        "active": True
    }

    response = requests.post(
        f"{ZAMMAD_URL}/api/v1/users",
        headers=HEADERS,
        json=payload,
        timeout=20
    )

    _raise_with_body(response)

    print(f"[ZAMMAD] Customer created: {email}")
    return response.json()


def create_ticket(customer_email, title, body, group="Service Desk", priority="2 normal"):
    # Ensure customer exists before ticket creation.
    create_customer_if_missing(
        email=customer_email,
        firstname="AI",
        lastname="Caller"
    )

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

    _raise_with_body(response)

    data = response.json()

    ticket_number = data.get("number")
    ticket_id = data.get("id")

    print(f"[ZAMMAD] Ticket created successfully. ID={ticket_id}, Number={ticket_number}")

    return {
        "success": True,
        "ticket_id": ticket_id,
        "ticket_number": ticket_number,
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

    _raise_with_body(response)

    data = response.json()

    return {
        "success": True,
        "ticket_id": data.get("id"),
        "ticket_number": data.get("number"),
        "raw": data
    }
