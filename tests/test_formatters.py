import unittest
from app.ticketing.formatters import format_ticket_description, format_ticket_comment


class TestTicketFormatters(unittest.TestCase):

    def test_format_ticket_description_structure(self):
        caller_info = {
            "name": "Haitham Al Sabahi",
            "employee_id": "1003",
            "department": "Finance",
            "phone": "+96890000003",
            "tier": "STANDARD",
        }
        desc = format_ticket_description(
            caller_info=caller_info,
            issue_category="Network & Connectivity",
            priority="High",
            call_outcome="Transferred to Queue 919",
            summary_of_issue="Office network outage affecting 80 workstations.",
            impact="80 users affected across Finance department",
            key_details=[{"field": "affected_site", "value": "Head Office Floor 2"}],
            troubleshooting_steps=["Advised checking physical ethernet cable and Wi-Fi SSID."],
            ai_resolution_note="Transferred call to NF L1 IT Support Queue (Extension 919).",
            requires_approval=False,
            is_emergency=False,
        )

        # Check for essential sections
        self.assertIn("User Affected", desc)
        self.assertIn("Haitham Al Sabahi", desc)
        self.assertIn("1003", desc)
        self.assertIn("Finance", desc)
        self.assertIn("+96890000003", desc)

        self.assertIn("Issue & Severity", desc)
        self.assertIn("Network & Connectivity", desc)
        self.assertIn("High", desc)

        self.assertIn("Summary of Issue", desc)
        self.assertIn("Office network outage affecting 80 workstations.", desc)
        self.assertIn("80 users affected", desc)
        self.assertIn("Affected Site", desc)

        self.assertIn("What AI Agent (Arif) Has Done", desc)
        self.assertIn("Identity verified via Employee ID: <strong>1003</strong>", desc)
        self.assertIn("Advised checking physical ethernet cable and Wi-Fi SSID.", desc)
        self.assertIn("Transferred call to NF L1 IT Support Queue", desc)

    def test_format_ticket_comment_structure(self):
        comment = format_ticket_comment(
            call_id="call-test-9999",
            caller_phone="+96890000003",
            duration="2m 15s",
            language="English",
            call_outcome="Transferred",
            transfer_target="919",
            transcript_lines=[
                "[17:39:22] Arif: Welcome to National Finance IT Support.",
                "[17:39:26] Caller: English please.",
                "[17:39:35] Arif: How can I help you?",
            ]
        )

        self.assertIn("Call Audit & Telephony Record", comment)
        self.assertIn("call-test-9999", comment)
        self.assertIn("+96890000003", comment)
        self.assertIn("Conversation Transcript", comment)
        self.assertIn("[17:39:22] Arif:", comment)
        self.assertIn("[17:39:26] Caller:", comment)
        self.assertIn("English please.", comment)

    def test_hardware_approval_banner_only_when_flagged(self):
        desc_no_approval = format_ticket_description(
            caller_info={"name": "Salim", "employee_id": "1005", "department": "Operations"},
            summary_of_issue="VPN login failure",
            requires_approval=False,
        )
        self.assertNotIn("ACTION REQUIRED: DEPARTMENT MANAGER APPROVAL", desc_no_approval)

        desc_approval = format_ticket_description(
            caller_info={"name": "Salim", "employee_id": "1005", "department": "Operations"},
            summary_of_issue="New Laptop Replacement",
            requires_approval=True,
        )
        self.assertIn("ACTION REQUIRED: DEPARTMENT MANAGER APPROVAL", desc_approval)
        self.assertIn("Operations", desc_approval)

    def test_emergency_banner_only_when_flagged(self):
        desc_normal = format_ticket_description(
            caller_info={"name": "Ahmed", "employee_id": "1001"},
            summary_of_issue="Password reset",
            is_emergency=False,
        )
        self.assertNotIn("SEV-1 CRITICAL OUTAGE ESCALATION", desc_normal)

        desc_emergency = format_ticket_description(
            caller_info={"name": "Ahmed", "employee_id": "1001"},
            summary_of_issue="Core datacenter link down",
            is_emergency=True,
        )
        self.assertIn("SEV-1 CRITICAL OUTAGE ESCALATION", desc_emergency)


if __name__ == "__main__":
    unittest.main()
