from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List


class BaseTicketingProvider(ABC):
    """
    Abstract interface for ticketing backends (Frappe Helpdesk, ERPNext, GLPI, etc.).
    """

    @abstractmethod
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
        """
        Create a new ticket in the ticketing backend.

        Args:
            customer_email: Email address of the caller/customer.
            title: Ticket subject/title.
            body: Full details/description.
            priority: Priority string ('low', 'normal', 'high', 'urgent', or 'p1', 'p2', etc.).
            category: Service category (e.g., 'Hardware', 'Network', 'Identity').
            caller_info: Dict containing verified name, employee_id, phone, department, tier.
            custom_fields: Arbitrary backend-specific attributes (call_id, recording_file, etc.).
            status: Initial ticket status ('Open', 'Pending', 'Resolved', 'Closed').

        Returns:
            Dict containing:
                - success: bool
                - ticket_id: str/int
                - ticket_number: str
                - raw: response dict from backend
        """
        pass

    @abstractmethod
    def get_or_create_customer(
        self,
        email: str,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        employee_id: Optional[str] = None,
        department: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Find an existing customer/contact by email or create one with accurate metadata.
        """
        pass

    @abstractmethod
    def lookup_assets(self, employee_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve IT assets (laptop, monitor, accessories) assigned to an employee.
        """
        pass

    @abstractmethod
    def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve ticket details by ID or Number.
        """
        pass

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """
        Verify connectivity and authentication to the ticketing backend.
        """
        pass
