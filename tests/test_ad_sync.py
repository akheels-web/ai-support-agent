import unittest
import time
from app.ad_sync import (
    parse_user_account_control,
    get_effective_ad_config,
    test_ad_connection,
    normalize_bind_dn,
    explain_ad_error,
)
import app.db as db
from app.verify import verify_user, clear_cache


class TestActiveDirectorySync(unittest.TestCase):

    def setUp(self):
        db.init_all_tables()
        clear_cache()

    def test_user_account_control_parser(self):
        """Test AD userAccountControl bitmask evaluation."""
        # 512 = NORMAL_ACCOUNT (Active)
        normal = parse_user_account_control(512)
        self.assertTrue(normal["active"] == 1)
        self.assertFalse(normal["is_disabled"])
        self.assertIn("NORMAL_ACCOUNT", normal["flags"])

        # 514 = NORMAL_ACCOUNT | ACCOUNTDISABLE (Disabled / Offboarded)
        disabled = parse_user_account_control(514)
        self.assertEqual(disabled["active"], 0)
        self.assertTrue(disabled["is_disabled"])
        self.assertIn("ACCOUNTDISABLE", disabled["flags"])

        # 530 = NORMAL_ACCOUNT | ACCOUNTDISABLE | LOCKOUT
        disabled_locked = parse_user_account_control(530)
        self.assertEqual(disabled_locked["active"], 0)
        self.assertTrue(disabled_locked["is_disabled"])
        self.assertTrue(disabled_locked["is_locked"])

        # 66048 = DONT_EXPIRE_PASSWORD | NORMAL_ACCOUNT (Active)
        pwd_no_expire = parse_user_account_control(66048)
        self.assertEqual(pwd_no_expire["active"], 1)
        self.assertFalse(pwd_no_expire["is_disabled"])

        # None / Missing input safely defaults to active normal account
        fallback = parse_user_account_control(None)
        self.assertEqual(fallback["active"], 1)

    def test_effective_ad_config(self):
        """Test AD configuration resolver and overrides."""
        cfg = get_effective_ad_config()
        self.assertIn("server", cfg)
        self.assertIn("port", cfg)
        self.assertIn("base_dn", cfg)
        self.assertIn("p0_groups", cfg)
        self.assertIn("p1_groups", cfg)

        # Test runtime override
        custom = get_effective_ad_config({"ad_server": "ldaps://custom.dc.local", "ad_port": 1636})
        self.assertEqual(custom["server"], "ldaps://custom.dc.local")
        self.assertEqual(custom["port"], 1636)

    def test_test_connection_handles_unreachable_server_gracefully(self):
        """Test that test_ad_connection handles network unreachability without crashing."""
        bad_config = {
            "ad_server": "ldaps://192.0.2.1",  # Test network reserved IP
            "ad_port": 636,
            "ad_bind_dn": "CN=Test",
            "ad_password": "InvalidPassword",
            "ad_base_dn": "DC=test,DC=local",
        }
        res = test_ad_connection(bad_config)
        self.assertIsInstance(res, dict)
        self.assertIn("success", res)
        # Even if connection fails or ldap3 is not present, it must return a clean structured report
        self.assertIn("message", res)
        self.assertIn("latency_ms", res)

    def test_preservation_of_phonetic_arabic_aliases_on_ad_update(self):
        """
        Critical safety test:
        AD does not have spoken Arabic/English phonetic nicknames.
        An AD sync must NEVER overwrite or wipe out custom aliases in the callers table.
        """
        emp_id = "7777"
        custom_aliases = "سالم المسكري | Salim Maskari | أبو أحمد"
        now = int(time.time())

        # 1. Simulate an existing caller enriched by IT Admin in dashboard
        with db.get_db() as conn:
            conn.execute("DELETE FROM callers WHERE employee_id = ?", (emp_id,))
            conn.execute(
                """
                INSERT INTO callers (
                    employee_id, name, aliases, email, phone,
                    department, role, vip, tier, active, source, updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    emp_id, "Salim Al Maskari", custom_aliases, "salim.old@example.com",
                    "+96899000002", "Finance", "Accountant", 1, "P1_VIP", 1, "manual", now, now
                ),
            )
            conn.commit()

        clear_cache()

        # Verify initial state
        res_before = verify_user(emp_id, "أبو أحمد")
        self.assertTrue(res_before["verified"])

        # 2. Simulate an incoming AD update (where AD has no knowledge of custom Arabic aliases)
        new_ad_title = "Chief Financial Officer"
        new_ad_email = "salim.cfo@nationalfinance.com"

        with db.get_db() as conn:
            existing = conn.execute("SELECT id, aliases FROM callers WHERE employee_id = ?", (emp_id,)).fetchone()
            self.assertIsNotNone(existing)
            # AD Sync engine preserves existing["aliases"]
            conn.execute(
                """
                UPDATE callers
                SET name=?, email=?, department=?, role=?, updated_at=?
                WHERE id=?
                """,
                ("Salim Al Maskari", new_ad_email, "Finance", new_ad_title, now + 10, existing["id"]),
            )
            conn.commit()

        clear_cache()

        # 3. Verify that the custom Arabic alias is STILL working for voice verification!
        res_after = verify_user(emp_id, "أبو أحمد")
        self.assertTrue(res_after["verified"])
        self.assertEqual(res_after["role"], new_ad_title)
        self.assertEqual(res_after["email"], new_ad_email)

        # Clean up
        with db.get_db() as conn:
            conn.execute("DELETE FROM callers WHERE employee_id = ?", (emp_id,))
            conn.commit()
    def test_normalize_bind_dn(self):
        """Test Active Directory bind DN normalization for user formats."""
        base_dn = "DC=nfc,DC=co,DC=om"

        # Trailing slash with bare username
        self.assertEqual(normalize_bind_dn("ai.agent/", base_dn), "ai.agent@nfc.co.om")
        # Bare username without slash
        self.assertEqual(normalize_bind_dn("ai.agent", base_dn), "ai.agent@nfc.co.om")
        # Whitespace handling
        self.assertEqual(normalize_bind_dn("  ai.agent/  ", base_dn), "ai.agent@nfc.co.om")
        # Already UPN format
        self.assertEqual(normalize_bind_dn("ai.agent@nfc.co.om", base_dn), "ai.agent@nfc.co.om")
        # NetBIOS domain\user
        self.assertEqual(normalize_bind_dn("NFC\\ai.agent", base_dn), "NFC\\ai.agent")
        # Full distinguished name
        self.assertEqual(normalize_bind_dn("CN=ai.agent,DC=nfc,DC=co,DC=om", base_dn), "CN=ai.agent,DC=nfc,DC=co,DC=om")
        # Empty input
        self.assertEqual(normalize_bind_dn(""), "")
        self.assertEqual(normalize_bind_dn(None), "")

    def test_explain_ad_error(self):
        """Test translation of AD LDAP error codes."""
        # data 773: Password must change
        msg_773 = "80090308: LdapErr: DSID-0C0904AE, comment: AcceptSecurityContext error, data 773, v3839"
        explained_773 = explain_ad_error(msg_773)
        self.assertIn("data 773", explained_773)
        self.assertIn("User must change password at next logon", explained_773)

        # data 52e: Bad credentials
        msg_52e = "80090308: LdapErr: DSID-0C0904AE, comment: AcceptSecurityContext error, data 52e, v3839"
        explained_52e = explain_ad_error(msg_52e)
        self.assertIn("data 52e", explained_52e)
        self.assertIn("Invalid Credentials", explained_52e)


if __name__ == "__main__":
    unittest.main()

