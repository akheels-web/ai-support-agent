import unittest
import os
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
os.environ["DB_PATH"] = str(BASE_DIR / "data" / "test_transcripts.db")

import app.db as db
from app.call_logger import create_call, update_call, close_call


class TestDtmfAndTranscripts(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        test_db = Path(os.environ["DB_PATH"])
        if test_db.exists():
            test_db.unlink()
        db.init_all_tables()

    @classmethod
    def tearDownClass(cls):
        test_db = Path(os.environ["DB_PATH"])
        if test_db.exists():
            test_db.unlink()
        try:
            with db.get_db() as conn:
                conn.execute("DELETE FROM calls WHERE call_id LIKE 'test_%'")
                conn.commit()
        except Exception:
            pass

    def test_transcript_column_and_persistence(self):
        call_id = f"test_{int(time.time() * 1000)}"
        create_call(call_id)

        # Update with transcript
        sample_transcript = "[10:00:01] Arif: Hello, how can I help?\n[10:00:05] Caller: My laptop is slow."
        sample_summary = "Caller: John (Emp ID: 1002) | Category: slow_computer | Issue: Slow laptop"

        update_call(call_id, transcript=sample_transcript, summary=sample_summary)

        # Fetch and verify
        row = db.fetch_one("SELECT transcript, summary FROM calls WHERE call_id=?", (call_id,))
        self.assertIsNotNone(row)
        self.assertEqual(row["transcript"], sample_transcript)
        self.assertEqual(row["summary"], sample_summary)

    def test_close_call_with_summary_and_transcript(self):
        call_id = f"test_close_{int(time.time() * 1000)}"
        create_call(call_id)

        t_data = "[10:05:00] Caller: Wifi disconnected\n[10:05:04] Arif: Please reconnect to NF-Corp."
        s_data = "Caller: Ahmed | Category: wifi_issue | Issue: Wifi disconnected | Ticket: HD-2026-0099"

        close_call(call_id, status="completed", summary=s_data, transcript=t_data)

        row = db.fetch_one("SELECT status, summary, transcript FROM calls WHERE call_id=?", (call_id,))
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["summary"], s_data)
        self.assertEqual(row["transcript"], t_data)


if __name__ == "__main__":
    unittest.main()
