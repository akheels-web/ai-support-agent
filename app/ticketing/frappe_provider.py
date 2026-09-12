import json
import logging
from typing import Dict, Any, Optional, List
import requests

from app.ticketing.base import BaseTicketingProvider

logger = logging.getLogger("frappe_provider")


class FrappeProvider(BaseTicketingProvider):
    """
    Ticketing provider for Frappe Helpdesk (HD Ticket) and ERPNext Support (Issue).
    """

    PRIORITY_MAP = {
        "1 low": "Low",
        "low": "Low",
        "2 normal": "Medium",
        "normal": "Medium",
        "medium": "Medium",
        "3 high": "High",
        "high": "High",
        "4 urgent": "Urgent",
        "urgent": "Urgent",
        "p1": "Urgent",
        "emergency": "Urgent",
        "critical": "Urgent",
    }

    def __init__(
        self,
        url: str,
        api_key: str,
        api_secret: str,
        ticket_doctype: str = "HD Ticket",
        default_team: Optional[str] = "IT Support",
        timeout: int = 10,
    ):
        self.url = url.rstrip("/")
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").strip()
        self.ticket_doctype = ticket_doctype.strip()
        self.default_team = default_team
        self.timeout = timeout

        self.headers = {
            "Authorization": f"token {self.api_key}:{self.api_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _normalize_priority(self, priority: str) -> str:
        key = (priority or "normal").strip().lower()
        return self.PRIORITY_MAP.get(key, "Medium")

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.url}{endpoint}"
        kwargs.setdefault("headers", self.headers)
        kwargs.setdefault("timeout", self.timeout)

        response = requests.request(method, url, **kwargs)
        if response.status_code >= 400:
            logger.error(f"[FRAPPE ERROR {response.status_code}] {response.text}")
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
        """
        Query or create user/contact in Frappe.
        """
        if not email:
            raise ValueError("Email is required to get or create contact")

        # 1. Search for existing HD Contact or User
        contact_doctype = "HD Contact" if self.ticket_doctype == "HD Ticket" else "Contact"
        try:
            filters = json.dumps([["email_id", "=", email]])
            resp = self._request("GET", f"/api/resource/{contact_doctype}?filters={filters}&fields=[\"*\"]")
            data = resp.json().get("data", [])
            if data:
                return data[0]
        except Exception as exc:
            logger.warning(f"Could not query {contact_doctype} by email ({exc}). Proceeding to create.")

        # 2. Create if not found
        payload = {
            "email_id": email,
            "first_name": name or email.split("@")[0],
            "phone": phone or "",
            "department": department or "",
        }
        if employee_id:
            payload["employee_id"] = employee_id

        try:
            resp = self._request("POST", f"/api/resource/{contact_doctype}", json=payload)
            return resp.json().get("data", {})
        except Exception as exc:
            logger.warning(f"Failed to create {contact_doctype}: {exc}. Using fallback dict.")
            return {"email_id": email, "first_name": name or "Caller"}

    HARDWARE_KEYWORDS = {
        "laptop", "desktop", "computer", "pc", "monitor", "screen", "keyboard",
        "mouse", "headset", "headphone", "dock", "docking", "charger", "adapter",
        "printer", "toner", "scanner", "phone", "hardware", "device", "cables",
        "replacement", "لوحة مفاتيح", "فأرة", "شاشة", "كمبيوتر", "شاحن", "طابعة"
    }

    def _is_hardware_request(self, title: str, body: str, category: Optional[str]) -> bool:
        if category and "hardware" in category.lower():
            return True
        combined = f"{title} {body}".lower()
        return any(kw in combined for kw in self.HARDWARE_KEYWORDS)

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

        # Ensure customer exists with real name & metadata
        caller_name = caller_info.get("name") or caller_info.get("verified_name")
        self.get_or_create_customer(
            email=customer_email,
            name=caller_name,
            phone=caller_info.get("phone"),
            employee_id=caller_info.get("employee_id"),
            department=caller_info.get("department"),
        )

        frappe_priority = self._normalize_priority(priority)

        # Check for hardware approval requirement
        is_hardware = (
            custom_fields.get("requires_approval")
            or category == "Hardware Request"
            or self._is_hardware_request(title, body, category)
        )

        ticket_status = status
        workflow_state = "Open"
        requires_approval = False
        approval_status = "Not Required"

        if is_hardware and status.lower() == "open":
            ticket_status = "Pending Approval"
            workflow_state = "Pending Approval"
            requires_approval = True
            approval_status = "Pending Manager Approval"
            category = category or "Hardware Request"

            approval_header = (
                "============================================================\n"
                "[ACTION REQUIRED: DEPARTMENT MANAGER APPROVAL]\n"
                "This hardware request was created via AI Support Agent (Arif).\n"
                "In accordance with National Finance IT governance, physical\n"
                "equipment dispatch requires Department Manager approval.\n"
                f"Caller Department: {caller_info.get('department', 'General')}\n"
                "Status: Pending Manager Approval\n"
                "============================================================\n\n"
            )
            body = approval_header + body

        # Build payload based on DocType
        if self.ticket_doctype == "HD Ticket":
            payload = {
                "subject": title,
                "description": body,
                "priority": frappe_priority,
                "status": ticket_status,
                "ticket_type": category or "Service Request",
                "customer": customer_email,
                "customer_name": caller_name or customer_email,
                "contact": customer_email,
            }
            if self.default_team:
                payload["team"] = self.default_team

            # Custom telephony, audit & approval attributes
            payload["custom_call_id"] = custom_fields.get("call_id", "")
            payload["custom_caller_phone"] = caller_info.get("phone", "")
            payload["custom_employee_id"] = caller_info.get("employee_id", "")
            payload["custom_tier"] = caller_info.get("tier", "STANDARD")
            payload["custom_recording_file"] = custom_fields.get("recording_file", "")
            payload["custom_ai_deflected"] = 1 if ticket_status.lower() in ("resolved", "closed") else 0
            payload["custom_requires_approval"] = 1 if requires_approval else 0
            payload["custom_approval_status"] = approval_status
            payload["custom_approver_role"] = "Department Manager" if requires_approval else ""
            payload["workflow_state"] = workflow_state

        else:
            # Fallback to ERPNext standard 'Issue' DocType
            payload = {
                "subject": title,
                "description": body,
                "priority": frappe_priority,
                "status": ticket_status,
                "raised_by": customer_email,
                "issue_type": category or "IT Support",
                "custom_call_id": custom_fields.get("call_id", ""),
                "custom_caller_phone": caller_info.get("phone", ""),
                "custom_employee_id": caller_info.get("employee_id", ""),
                "custom_tier": caller_info.get("tier", "STANDARD"),
                "custom_ai_deflected": 1 if ticket_status.lower() in ("resolved", "closed") else 0,
                "custom_requires_approval": 1 if requires_approval else 0
            }

        resp = self._request("POST", f"/api/resource/{self.ticket_doctype}", json=payload)
        ticket_data = resp.json().get("data", {})

        ticket_id = ticket_data.get("name")
        ticket_number = str(ticket_id)

        logger.info(f"[FRAPPE] Created {self.ticket_doctype} {ticket_number} (status={ticket_status}, approval={approval_status})")

        return {
            "success": True,
            "ticket_id": ticket_id,
            "ticket_number": ticket_number,
            "priority": frappe_priority,
            "status": ticket_status,
            "requires_approval": requires_approval,
            "approval_status": approval_status,
            "approval_note": "Requires Department Manager approval in the IT Helpdesk before IT dispatch." if requires_approval else "",
            "raw": ticket_data,
        }

    def lookup_assets(self, employee_id: str) -> List[Dict[str, Any]]:
        """
        Query assets assigned to the caller via ERPNext Asset DocType.
        """
        if not employee_id:
            return []

        try:
            filters = json.dumps([["custodian", "=", employee_id]])
            resp = self._request(
                "GET",
                f"/api/resource/Asset?filters={filters}&fields=[\"name\",\"item_name\",\"asset_category\",\"status\",\"serial_no\"]",
            )
            return resp.json().get("data", [])
        except Exception as exc:
            logger.warning(f"[FRAPPE] Failed to lookup assets for employee {employee_id}: {exc}")
            return []

    def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._request("GET", f"/api/resource/{self.ticket_doctype}/{ticket_id}")
            return resp.json().get("data")
        except Exception as exc:
            logger.warning(f"[FRAPPE] Failed to get ticket {ticket_id}: {exc}")
            return None

    def health_check(self) -> Dict[str, Any]:
        try:
            resp = self._request("GET", f"/api/resource/{self.ticket_doctype}?limit=1")
            return {"healthy": True, "status_code": resp.status_code, "doctype": self.ticket_doctype}
        except Exception as exc:
            return {"healthy": False, "error": str(exc), "doctype": self.ticket_doctype}
