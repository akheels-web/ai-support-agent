import os
import unittest
import tempfile
import time
from pathlib import Path

# Ensure temporary SQLite DB for test isolation
temp_dir = tempfile.TemporaryDirectory()
temp_db_path = str(Path(temp_dir.name) / "test_dashboard.db")
os.environ["DB_PATH"] = temp_db_path
os.environ["DATABASE_URL"] = ""

import app.db as db
from app.db import RowDict
import app.call_logger as call_logger
import app.security_guard as security_guard


class TestDatabaseAdapter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db.init_all_tables()

    def test_row_dict_backward_compatibility(self):
        d = RowDict({"username": "ahmed", "role": "admin", "active": 1})
        self.assertEqual(d["username"], "ahmed")
        self.assertEqual(d["role"], "admin")
        self.assertEqual(d[0], "ahmed")
        self.assertEqual(d[1], "admin")
        self.assertEqual(d[2], 1)

    def test_table_initialization_and_crud(self):
        db.init_all_tables()
        conn = db.get_db()
        try:
            # Clean up prior test data if exists
            conn.execute("DELETE FROM users WHERE username = ?", ("test_emp_01",))
            conn.commit()

            # Test insert into users
            conn.execute(
                "INSERT INTO users (username, password_hash, role, active, created_at) VALUES (?, ?, ?, 1, ?)",
                ("test_emp_01", "hash123", "user", int(time.time()))
            )
            conn.commit()

            # Test query
            row = conn.execute("SELECT * FROM users WHERE username = ?", ("test_emp_01",)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["username"], "test_emp_01")
            self.assertEqual(row["role"], "user")
        finally:
            conn.close()

    def test_atomic_rate_limiting(self):
        key = "ip:192.168.1.50"
        security_guard.reset_rate_limit(key)

        # Allow up to 3 calls in 60 seconds
        res1 = security_guard.check_rate_limit(key, limit=3, window_seconds=60, lock_seconds=300)
        self.assertTrue(res1["allowed"])

        res2 = security_guard.check_rate_limit(key, limit=3, window_seconds=60, lock_seconds=300)
        self.assertTrue(res2["allowed"])

        res3 = security_guard.check_rate_limit(key, limit=3, window_seconds=60, lock_seconds=300)
        self.assertTrue(res3["allowed"])

        # 4th call should trigger rate limit lock
        res4 = security_guard.check_rate_limit(key, limit=3, window_seconds=60, lock_seconds=300)
        self.assertFalse(res4["allowed"])
        self.assertIn(res4["reason"], ("limit_exceeded", "rate_limited"))
        self.assertGreater(res4["retry_after"], 0)

        # Check is_locked
        lock_secs = security_guard.is_locked(key)
        self.assertGreater(lock_secs, 0)

        # Reset rate limit
        security_guard.reset_rate_limit(key)
        self.assertEqual(security_guard.is_locked(key), 0)

    def test_call_logger_lifecycle(self):
        call_id = "test_call_abc123"
        call_logger.create_call(call_id, status="in_progress")

        call_logger.update_call(
            call_id,
            caller_number="+96890001111",
            employee_id="EMP-777",
            verified_name="Fatima Al Harthi",
            language="en",
            tier="P1_VIP",
            is_vip=1,
            ticket_number="HD-1002",
            ticket_created=1,
            ai_deflected=1,
            resolution_type="AI_Resolved"
        )

        call_logger.close_call(call_id, status="resolved")

        row = db.fetch_one("SELECT * FROM calls WHERE call_id = ?", (call_id,))
        self.assertIsNotNone(row)
        self.assertEqual(row["caller_number"], "+96890001111")
        self.assertEqual(row["employee_id"], "EMP-777")
        self.assertEqual(row["verified_name"], "Fatima Al Harthi")
        self.assertEqual(row["tier"], "P1_VIP")
        self.assertEqual(row["is_vip"], 1)
        self.assertEqual(row["ticket_number"], "HD-1002")
        self.assertEqual(row["ai_deflected"], 1)
        self.assertEqual(row["status"], "resolved")
        self.assertIsNotNone(row["end_time"])


if __name__ == "__main__":
    unittest.main()
