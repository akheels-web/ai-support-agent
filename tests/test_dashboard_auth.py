import unittest
from dashboard.app import hash_password, verify_password


class TestDashboardAuth(unittest.TestCase):

    def test_salted_password_hashing(self):
        pwd = "SecurePassword123!"
        hashed = hash_password(pwd)
        self.assertIn("$", hashed)
        self.assertTrue(verify_password(pwd, hashed))
        self.assertFalse(verify_password("WrongPassword", hashed))

    def test_legacy_hash_compatibility(self):
        # Legacy static salt "ai-support-agent" for "admin123"
        import hashlib
        legacy_hash = hashlib.pbkdf2_hmac("sha256", b"admin123", b"ai-support-agent", 100000).hex()
        self.assertNotIn("$", legacy_hash)

        # verify_password must successfully verify legacy hash
        self.assertTrue(verify_password("admin123", legacy_hash))
        self.assertFalse(verify_password("wrongadmin", legacy_hash))


if __name__ == "__main__":
    unittest.main()
