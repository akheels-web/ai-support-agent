import unittest
from unittest.mock import patch, MagicMock
from app.ticketing.frappe_provider import FrappeProvider
from app.ticketing import get_ticketing_client, reset_ticketing_client


class TestFrappeTicketingProvider(unittest.TestCase):

    def setUp(self):
        reset_ticketing_client()

    def test_frappe_priority_normalization(self):
        provider = FrappeProvider(url="http://mock-frappe:8000", api_key="key", api_secret="secret")
        self.assertEqual(provider._normalize_priority("1 low"), "Low")
        self.assertEqual(provider._normalize_priority("2 normal"), "Medium")
        self.assertEqual(provider._normalize_priority("3 high"), "High")
        self.assertEqual(provider._normalize_priority("urgent"), "Urgent")
        self.assertEqual(provider._normalize_priority("emergency"), "Urgent")

    @patch("requests.request")
    def test_frappe_create_hardware_ticket_with_manager_approval(self, mock_request):
        mock_resp_contact = MagicMock()
        mock_resp_contact.status_code = 200
        mock_resp_contact.json.return_value = {"data": [{"name": "CUST-001", "email_id": "test@example.com"}]}

        mock_resp_ticket = MagicMock()
        mock_resp_ticket.status_code = 200
        mock_resp_ticket.json.return_value = {"data": {"name": "HD-2026-00042"}}

        mock_request.side_effect = [mock_resp_contact, mock_resp_ticket]

        provider = FrappeProvider(url="http://mock-frappe:8000", api_key="test_key", api_secret="test_secret")
        result = provider.create_ticket(
            customer_email="test@example.com",
            title="Broken Laptop Screen",
            body="User dropped laptop.",
            priority="high",
            caller_info={"name": "Ahmed Al Balushi", "phone": "+96890000001", "employee_id": "1001", "tier": "P1_VIP"},
            custom_fields={"call_id": "call_12345"},
            status="Open"
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["ticket_id"], "HD-2026-00042")
        self.assertEqual(result["ticket_number"], "HD-2026-00042")
        self.assertEqual(result["priority"], "High")
        self.assertEqual(result["status"], "Pending Approval")
        self.assertTrue(result["requires_approval"])
        self.assertEqual(result["approval_status"], "Pending Manager Approval")

        # Verify auth headers
        self.assertEqual(provider.headers["Authorization"], "token test_key:test_secret")

    @patch("requests.request")
    def test_frappe_lookup_assets(self, mock_request):
        mock_resp_assets = MagicMock()
        mock_resp_assets.status_code = 200
        mock_resp_assets.json.return_value = {
            "data": [
                {"name": "ASSET-001", "item_name": "Dell Latitude 5440", "serial_no": "DL123456"}
            ]
        }
        mock_request.return_value = mock_resp_assets

        provider = FrappeProvider(url="http://mock-frappe:8000", api_key="test_key", api_secret="test_secret")
        assets = provider.lookup_assets("1001")
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["item_name"], "Dell Latitude 5440")


if __name__ == "__main__":
    unittest.main()
