import os
import unittest
import tempfile
import sqlite3
import time
from pathlib import Path

import app.db as db
from app.verify import lookup_caller_by_phone, verify_user, clear_cache


class TestCallersManagement(unittest.TestCase):

    def setUp(self):
        # Initialize tables
        db.init_all_tables()
        clear_cache()

    def test_callers_table_seeded(self):
        """Verify callers table is seeded from users.csv on initial run."""
        with db.get_db() as conn:
            rows = conn.execute("SELECT * FROM callers ORDER BY id ASC").fetchall()
            self.assertGreater(len(rows), 0)

            # Check CEO record
            ceo = next((r for r in rows if r["employee_id"] == "1000"), None)
            self.assertIsNotNone(ceo)
            self.assertEqual(ceo["tier"], "P0_EXECUTIVE")
            self.assertEqual(ceo["active"], 1)

    def test_onboard_new_caller_and_verify(self):
        """Test onboarding a new caller into DB and verifying via verify_user."""
        new_emp_id = "9999"
        new_name = "Test Employee"
        new_phone = "+96899999999"
        now = int(time.time())

        with db.get_db() as conn:
            # Clean up if exists
            conn.execute("DELETE FROM callers WHERE employee_id = ?", (new_emp_id,))
            conn.execute(
                """
                INSERT INTO callers (
                    employee_id, name, aliases, email, phone,
                    department, role, vip, tier, active, source, updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_emp_id, new_name, "موظف اختبار|Test Emp", "test@nationalfinance.com",
                    new_phone, "Testing", "QA Engineer", 1, "P1_VIP", 1, "test", now, now
                ),
            )
            conn.commit()

        clear_cache()

        # Check phone lookup
        caller = lookup_caller_by_phone(new_phone)
        self.assertIsNotNone(caller)
        self.assertEqual(caller["employee_id"], new_emp_id)
        self.assertEqual(caller["tier"], "P1_VIP")

        # Check voice verification by name
        res = verify_user(new_emp_id, "Test Employee")
        self.assertTrue(res["verified"])
        self.assertEqual(res["tier"], "P1_VIP")

        # Check voice verification by Arabic alias
        res_alias = verify_user(new_emp_id, "موظف اختبار")
        self.assertTrue(res_alias["verified"])

        # Clean up
        with db.get_db() as conn:
            conn.execute("DELETE FROM callers WHERE employee_id = ?", (new_emp_id,))
            conn.commit()
        clear_cache()

    def test_offboard_caller_rejection(self):
        """Test that deactivating/offboarding a caller immediately blocks phone and voice verification."""
        emp_id = "8888"
        phone = "+96888888888"
        now = int(time.time())

        with db.get_db() as conn:
            conn.execute("DELETE FROM callers WHERE employee_id = ?", (emp_id,))
            conn.execute(
                """
                INSERT INTO callers (
                    employee_id, name, aliases, email, phone,
                    department, role, vip, tier, active, source, updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (emp_id, "Departing Worker", "", "departing@example.com", phone, "Operations", "Clerk", 0, "STANDARD", 1, "test", now, now),
            )
            conn.commit()

        clear_cache()
        # Active caller should pass
        res_active = verify_user(emp_id, "Departing Worker")
        self.assertTrue(res_active["verified"])

        # Offboard caller (active = 0)
        with db.get_db() as conn:
            conn.execute("UPDATE callers SET active = 0 WHERE employee_id = ?", (emp_id,))
            conn.commit()
        clear_cache()

        # Phone lookup must ignore deactivated caller
        caller = lookup_caller_by_phone(phone)
        self.assertIsNone(caller)

        # verify_user must reject with specific account_deactivated reason
        res_inactive = verify_user(emp_id, "Departing Worker")
        self.assertFalse(res_inactive["verified"])
        self.assertEqual(res_inactive["reason"], "account_deactivated")

        # Re-activate caller (active = 1)
        with db.get_db() as conn:
            conn.execute("UPDATE callers SET active = 1 WHERE employee_id = ?", (emp_id,))
            conn.commit()
        clear_cache()

        res_reactivated = verify_user(emp_id, "Departing Worker")
        self.assertTrue(res_reactivated["verified"])

        # Clean up
        with db.get_db() as conn:
            conn.execute("DELETE FROM callers WHERE employee_id = ?", (emp_id,))
            conn.commit()
        clear_cache()

    def test_sync_callers_to_csv(self):
        """Test syncing database callers to a target CSV file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as tmp:
            tmp_path = tmp.name

        try:
            ok = db.sync_callers_to_csv(tmp_path)
            self.assertTrue(ok)
            self.assertTrue(os.path.exists(tmp_path))

            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("employee_id,name,aliases,email,phone,department,vip,role,tier,active", content)
                self.assertIn("Khalid Al Harthy", content)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
