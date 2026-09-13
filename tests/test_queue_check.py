import unittest
from unittest.mock import patch, MagicMock
from app.transfer import (
    resolve_queue_name,
    resolve_queue_target,
    check_queue_availability,
    QUEUE_STANDARD,
    QUEUE_EXECUTIVE,
    QUEUE_EMERGENCY,
    QUEUE_NAME_STANDARD,
    QUEUE_NAME_EXECUTIVE,
    QUEUE_NAME_EMERGENCY,
)


class TestQueueAvailability(unittest.TestCase):

    def test_queue_name_resolution(self):
        self.assertEqual(resolve_queue_name("standard"), QUEUE_NAME_STANDARD)
        self.assertEqual(resolve_queue_name("7001"), QUEUE_NAME_STANDARD)
        self.assertEqual(resolve_queue_name("executive"), QUEUE_NAME_EXECUTIVE)
        self.assertEqual(resolve_queue_name("ceo"), QUEUE_NAME_EXECUTIVE)
        self.assertEqual(resolve_queue_name("7002"), QUEUE_NAME_EXECUTIVE)
        self.assertEqual(resolve_queue_name("emergency"), QUEUE_NAME_EMERGENCY)
        self.assertEqual(resolve_queue_name("critical"), QUEUE_NAME_EMERGENCY)
        self.assertEqual(resolve_queue_name("7003"), QUEUE_NAME_EMERGENCY)

    def test_emergency_always_available(self):
        # Emergency queues must fail open / always allow transfer
        res = check_queue_availability("emergency")
        self.assertTrue(res.get("available"))
        self.assertTrue(res.get("emergency"))

        res2 = check_queue_availability("critical")
        self.assertTrue(res2.get("available"))

    @patch("app.transfer.ASTERISK_AMI_USER", "")
    def test_fallback_when_credentials_not_configured(self):
        res = check_queue_availability("standard")
        self.assertTrue(res.get("available"))
        self.assertEqual(res.get("reason"), "ami_not_configured_fallback")

    @patch("app.transfer._ami_connect")
    @patch("app.transfer.ASTERISK_AMI_USER", "admin")
    @patch("app.transfer.ASTERISK_AMI_SECRET", "secret")
    def test_queue_available_when_agents_online(self, mock_connect):
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [
            b"Response: Success\r\nMessage: Queue summary will follow\r\n\r\n",
            b"Event: QueueSummary\r\nQueue: it-support\r\nLoggedIn: 3\r\nAvailable: 2\r\nCallers: 0\r\n\r\n",
            b"Event: QueueSummaryComplete\r\n\r\n"
        ]
        mock_connect.return_value = mock_sock

        res = check_queue_availability("standard")
        self.assertTrue(res["available"])
        self.assertEqual(res["logged_in"], 3)
        self.assertEqual(res["available_agents"], 2)
        self.assertEqual(res["reason"], "agents_available")

    @patch("app.transfer._ami_connect")
    @patch("app.transfer.ASTERISK_AMI_USER", "admin")
    @patch("app.transfer.ASTERISK_AMI_SECRET", "secret")
    def test_queue_blocked_when_zero_agents(self, mock_connect):
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [
            b"Response: Success\r\n\r\n",
            b"Event: QueueSummary\r\nQueue: it-support\r\nLoggedIn: 0\r\nAvailable: 0\r\nCallers: 1\r\n\r\n",
            b"Event: QueueSummaryComplete\r\n\r\n"
        ]
        mock_connect.return_value = mock_sock

        res = check_queue_availability("standard")
        self.assertFalse(res["available"])
        self.assertEqual(res["logged_in"], 0)
        self.assertEqual(res["available_agents"], 0)
        self.assertEqual(res["reason"], "no_agents_available")


if __name__ == "__main__":
    unittest.main()
