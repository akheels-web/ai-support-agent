import unittest
from unittest.mock import patch, MagicMock
from app.ticketing.frappe_provider import FrappeProvider


class TestAIGuardrails(unittest.TestCase):

    def test_non_it_scope_keywords_detected(self):
        non_it_queries = [
            ("Personal Loan Inquiry", "Can I apply for a car loan of 5000 OMR?"),
            ("Credit Card Balance", "What is my credit card outstanding balance?"),
            ("Bank Statement", "I need bank statement for my auto finance application."),
            ("Interest Rates", "What is the current interest rate for home loans?"),
            ("طلب تمويل", "أريد تقديم طلب للحصول على قرض سيارة من الشركة"),
        ]

        non_it_terms = {
            "loan", "personal loan", "car loan", "auto loan", "vehicle finance",
            "interest rate", "credit card", "debit card", "account balance",
            "bank statement", "branch location", "salary advance", "payroll",
            "قرض", "تمويل", "سلفة", "كشف حساب", "بطاقة ائتمان"
        }

        for title, desc in non_it_queries:
            combined = f"{title} {desc}".lower()
            is_non_it = any(term in combined for term in non_it_terms)
            self.assertTrue(is_non_it, f"Expected '{title}' to be flagged as non-IT inquiry.")

    def test_description_quality_gate(self):
        invalid_descriptions = [
            "",
            "hi",
            "test",
            "broken",
            "issue",
            "help",
            "pls fix",
            "it issue",
        ]

        for desc in invalid_descriptions:
            is_valid = len(desc.strip()) >= 10 and desc.strip().lower() not in {
                "hi", "hello", "test", "issue", "problem", "help", "broken", "it issue", "none", "n/a"
            }
            self.assertFalse(is_valid, f"Expected '{desc}' to fail quality gate.")

        valid_descriptions = [
            "Cannot connect to Muscat VPN server from home network.",
            "Outlook keeps prompting for password repeatedly.",
            "Dell laptop screen flickers when connected to dock.",
        ]
        for desc in valid_descriptions:
            is_valid = len(desc.strip()) >= 10 and desc.strip().lower() not in {
                "hi", "hello", "test", "issue", "problem", "help", "broken", "it issue", "none", "n/a"
            }
            self.assertTrue(is_valid, f"Expected '{desc}' to pass quality gate.")

    @patch("requests.request")
    def test_hardware_request_requires_manager_approval(self, mock_request):
        mock_resp_contact = MagicMock()
        mock_resp_contact.status_code = 200
        mock_resp_contact.json.return_value = {"data": [{"name": "CUST-002", "email_id": "khalid@example.com"}]}

        mock_resp_ticket = MagicMock()
        mock_resp_ticket.status_code = 200
        mock_resp_ticket.json.return_value = {"data": {"name": "HD-2026-00099"}}

        mock_request.side_effect = [mock_resp_contact, mock_resp_ticket]

        provider = FrappeProvider(url="http://mock-frappe:8000", api_key="key", api_secret="secret")

        # Create a hardware ticket
        result = provider.create_ticket(
            customer_email="khalid@example.com",
            title="New Laptop Request",
            body="Employee requires new laptop replacement for remote work.",
            priority="normal",
            category="Hardware Request",
            caller_info={
                "name": "Khalid Al Harthy",
                "employee_id": "1002",
                "department": "Executive Management",
                "tier": "P0_EXECUTIVE"
            },
            status="Open"
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "Pending Approval")
        self.assertTrue(result["requires_approval"])
        self.assertEqual(result["approval_status"], "Pending Manager Approval")
        self.assertIn("Requires Department Manager approval", result["approval_note"])

    @patch("requests.request")
    def test_software_request_no_manager_approval(self, mock_request):
        mock_resp_contact = MagicMock()
        mock_resp_contact.status_code = 200
        mock_resp_contact.json.return_value = {"data": [{"name": "CUST-003", "email_id": "salim@example.com"}]}

        mock_resp_ticket = MagicMock()
        mock_resp_ticket.status_code = 200
        mock_resp_ticket.json.return_value = {"data": {"name": "HD-2026-00100"}}

        mock_request.side_effect = [mock_resp_contact, mock_resp_ticket]

        provider = FrappeProvider(url="http://mock-frappe:8000", api_key="key", api_secret="secret")

        # Create a standard software ticket
        result = provider.create_ticket(
            customer_email="salim@example.com",
            title="Teams Audio Glitch",
            body="Microsoft Teams microphone drops after 5 minutes into calls.",
            priority="normal",
            category="Service Desk",
            caller_info={
                "name": "Salim Al Maskari",
                "employee_id": "1003",
                "department": "Finance",
                "tier": "STANDARD"
            },
            status="Open"
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "Open")
        self.assertFalse(result["requires_approval"])
        self.assertEqual(result["approval_status"], "Not Required")

    def test_no_backend_tool_names_in_agent_facing_prompts_or_tools(self):
        from app.openai_realtime_bridge import SYSTEM_PROMPT, TOOLS

        forbidden = ["frappe", "zammad", "erpnext"]

        # 1. Check tools descriptions
        for tool in TOOLS:
            desc = tool.get("description", "").lower()
            for word in forbidden:
                self.assertNotIn(word, desc, f"Tool '{tool.get('name')}' description exposes backend name '{word}'.")

        # 2. Check SYSTEM_PROMPT spoken dialogs
        # Extract quoted spoken sentences in SYSTEM_PROMPT
        import re
        spoken_dialogs = re.findall(r'"([^"]*)"', SYSTEM_PROMPT)
        for dialog in spoken_dialogs:
            dialog_lower = dialog.lower()
            for word in forbidden:
                self.assertNotIn(word, dialog_lower, f"Spoken dialog '{dialog}' exposes backend name '{word}'.")

    def test_repeat_ticket_and_digit_by_digit(self):
        from app.openai_realtime_bridge import digit_by_digit
        self.assertEqual(digit_by_digit("HD-2026-0012"), "H D - 2 0 2 6 - 0 0 1 2")
        self.assertEqual(digit_by_digit("1002"), "1 0 0 2")

    def test_callback_request_tool_schema(self):
        from app.openai_realtime_bridge import TOOLS
        callback_tool = next((t for t in TOOLS if t.get("name") == "request_callback"), None)
        self.assertIsNotNone(callback_tool)
        self.assertIn("preferred_time", callback_tool["parameters"]["required"])


if __name__ == "__main__":
    unittest.main()

