import unittest
from app.transfer import resolve_queue_target, QUEUE_STANDARD, QUEUE_EXECUTIVE, QUEUE_EMERGENCY


class TestTransferRouting(unittest.TestCase):

    def test_queue_resolution(self):
        self.assertEqual(resolve_queue_target("standard"), QUEUE_STANDARD)
        self.assertEqual(resolve_queue_target("executive"), QUEUE_EXECUTIVE)
        self.assertEqual(resolve_queue_target("ceo"), QUEUE_EXECUTIVE)
        self.assertEqual(resolve_queue_target("emergency"), QUEUE_EMERGENCY)
        self.assertEqual(resolve_queue_target("p1"), QUEUE_EMERGENCY)
        self.assertEqual(resolve_queue_target("custom", extension="7777"), "7777")


if __name__ == "__main__":
    unittest.main()
