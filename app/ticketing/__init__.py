import logging
from typing import Optional
from app.ticketing.base import BaseTicketingProvider

logger = logging.getLogger("ticketing")

_client_instance: Optional[BaseTicketingProvider] = None


def get_ticketing_client() -> BaseTicketingProvider:
    """
    Factory to obtain the configured ticketing client (Frappe Helpdesk / ERPNext).
    """
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    from app.config import (
        FRAPPE_URL,
        FRAPPE_API_KEY,
        FRAPPE_API_SECRET,
        FRAPPE_TICKET_DOCTYPE,
        FRAPPE_DEFAULT_TEAM,
    )
    from app.ticketing.frappe_provider import FrappeProvider

    logger.info(f"[TICKETING] Initializing Frappe Helpdesk client at {FRAPPE_URL}")
    _client_instance = FrappeProvider(
        url=FRAPPE_URL,
        api_key=FRAPPE_API_KEY,
        api_secret=FRAPPE_API_SECRET,
        ticket_doctype=FRAPPE_TICKET_DOCTYPE,
        default_team=FRAPPE_DEFAULT_TEAM,
    )

    return _client_instance


def reset_ticketing_client():
    """Reset singleton instance (useful for testing or dynamic config reloading)."""
    global _client_instance
    _client_instance = None
