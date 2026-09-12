import logging
from typing import Dict, Any, Optional, List
import requests

from app.ticketing.base import BaseTicketingProvider

logger = logging.getLogger("zammad_provider")


class ZammadProvider(BaseTicketingProvider):
    """
    Ticketing provider for Zammad REST API.
    """

    PRIORITY_MAP = {
        "1 low": "1 low",
        "low": "1 low",
        "2 normal": "2 normal",
        "normal": "2 normal",
        "medium": "2 normal",
        "3 high": "3 high",
        "high": "3 high",
        "4 urgent": "3 high",
        "urgent": "3 high",
        "p1": "3 high",
        "emergency": "3 high",
        "critical": "3 high",
    }

    def __init__(
        self,
        url: str,
        token: str,
        default_group: str = "Service Desk",
        timeout: int = 8,
    ):
        self.url = url.rstrip("/")
        self.token = (token or "").strip()
        self.default_group = default_group
        self.timeout = timeout

        self.headers = {
            "Authorization": f"Token token={self.token}",
            "Content-Type": "application/json",
        }

    def _normalize_priority(self, priority: str) -> str:
        key = (priority or "normal").strip().lower()
        return self.PRIORITY_MAP.get(key, "2 normal")

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.url}{endpoint}"
        kwargs.setdefault("headers", self.headers)
        kwargs.setdefault("timeout", self.timeout)

        response = requests.request(method, url, **kwargs)
        if response.status_code >= 400:
            logger.error(f"[ZAMMAD ERROR {response.status_code}] {response.text}")
            response.raise_for_status()
        return response

    def get_or_create_customer(
        self,
        email: str,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        employee_id: Optional[str] = None,
        department: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not email:
            raise ValueError("customer_email is required")

        # 1. Search for existing user
        try:
            resp = self._request("GET", "/api/v1/users/search", params={"query": email})
            users = resp.json()
            if isinstance(users, list):
                for u in users:
                    if str(u.get("email", "")).lower() == email.lower():
                        return u
        except Exception as exc:
            logger.warning(f"[ZAMMAD] Search customer failed ({exc}). Proceeding to create.")

        # 2. Split name into firstname and lastname properly
        if name and " " in name.strip():
            parts = name.strip().split(" ", 1)
            firstname, lastname = parts[0], parts[1]
        elif name:
            firstname, lastname = name.strip(), "Employee"
        else:
            firstname, lastname = "National Finance", "Caller"

        payload = {
            "firstname": firstname,
            "lastname": lastname,
            "email": email,
            "phone": phone or "",
            "department": department or "",
            "roles": ["Customer"],
            "active": True,
        }

        try:
            resp = self._request("POST", "/api/v1/users", json=payload)
            return resp.json()
        except Exception as exc:
            logger.warning(f"[ZAMMAD] Create customer failed ({exc}).")
            return {"email": email, "firstname": firstname, "lastname": lastname}

    def create_ticket(
        self,
        customer_email: str,
        title: str,
        body: str,
        priority: str = "normal",
        category: Optional[str] = None,
        caller_info: Optional[Dict[str, Any]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        status: str = "Open",
    ) -> Dict[str, Any]:
        if not customer_email:
            raise ValueError("customer_email is required")

        caller_info = caller_info or {}
        custom_fields = custom_fields or {}

        caller_name = caller_info.get("name") or caller_info.get("verified_name")
        self.get_or_create_customer(
            email=customer_email,
            name=caller_name,
            phone=caller_info.get("phone"),
            employee_id=caller_info.get("employee_id"),
            department=caller_info.get("department"),
        )

        zammad_priority = self._normalize_priority(priority)
        group = category or self.default_group

        # Append metadata to body
        metadata_lines = []
        if custom_fields.get("call_id"):
            metadata_lines.append(f"Asterisk Call ID: {custom_fields['call_id']}")
        if caller_info.get("phone"):
            metadata_lines.append(f"Caller Phone: {caller_info['phone']}")
        if caller_info.get("tier"):
            metadata_lines.append(f"Caller Tier: {caller_info['tier']}")
        if custom_fields.get("recording_file"):
            metadata_lines.append(f"Call Recording: {custom_fields['recording_file']}")
        if status.lower() in ("resolved", "closed"):
            metadata_lines.append("Resolution Status: Resolved by AI Agent")

        if metadata_lines:
            body = body + "\n\n--- Telephony & Metadata ---\n" + "\n".join(metadata_lines)

        payload = {
            "title": title,
            "group": group,
            "customer": customer_email,
            "priority": zammad_priority,
            "article": {
                "subject": title,
                "body": body,
                "type": "phone",
                "internal": False,
            },
        }

        try:
            resp = self._request("POST", "/api/v1/tickets", json=payload)
            data = resp.json()
        except requests.exceptions.HTTPError as exc:
            if group != self.default_group:
                logger.warning(f"[ZAMMAD] Retrying ticket with fallback group: {self.default_group}")
                payload["group"] = self.default_group
                resp = self._request("POST", "/api/v1/tickets", json=payload)
                data = resp.json()
            else:
                raise exc

        ticket_number = str(data.get("number") or data.get("id"))
        ticket_id = data.get("id")

        return {
            "success": True,
            "ticket_id": ticket_id,
            "ticket_number": ticket_number,
            "priority": zammad_priority,
            "status": status,
            "raw": data,
        }

    def lookup_assets(self, employee_id: str) -> List[Dict[str, Any]]:
        # Zammad does not provide native IT asset management
        return []

    def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._request("GET", f"/api/v1/tickets/{ticket_id}")
            return resp.json()
        except Exception as exc:
            logger.warning(f"[ZAMMAD] Failed to get ticket {ticket_id}: {exc}")
            return None

    def health_check(self) -> Dict[str, Any]:
        try:
            resp = self._request("GET", "/api/v1/tickets?per_page=1")
            return {"healthy": True, "status_code": resp.status_code}
        except Exception as exc:
            return {"healthy": False, "error": str(exc)}
