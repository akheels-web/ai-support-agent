"""
Backward-compatibility adapter for legacy imports of app.zammad_api.
Delegates to the modern app.ticketing provider framework.
"""
from app.ticketing import get_ticketing_client


def create_ticket(customer_email, title, body, group=None, priority="2 normal"):
    client = get_ticketing_client()
    result = client.create_ticket(
        customer_email=customer_email,
        title=title,
        body=body,
        priority=priority,
        category=group,
    )
    return result


def find_user_by_email(email):
    client = get_ticketing_client()
    return client.get_or_create_customer(email=email)


def create_customer_if_missing(email, firstname="AI", lastname="Caller"):
    client = get_ticketing_client()
    full_name = f"{firstname} {lastname}".strip()
    return client.get_or_create_customer(email=email, name=full_name)