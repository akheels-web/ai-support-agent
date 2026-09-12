import unittest
from app.verify import lookup_caller_by_phone, verify_user, normalize_digits, clear_cache


class TestVerifyAndEscalation(unittest.TestCase):

    def setUp(self):
        clear_cache()

    def test_digit_normalization(self):
        self.assertEqual(normalize_digits("1002"), "1002")
        self.assertEqual(normalize_digits("one zero zero two"), "1002")
        self.assertEqual(normalize_digits("واحد صفر صفر اثنين"), "1002")
        self.assertEqual(normalize_digits("One Zero One Zero"), "1010")

    def test_executive_caller_id_lookup(self):
        # CEO Phone
        ceo = lookup_caller_by_phone("+96899000001")
        self.assertIsNotNone(ceo)
        self.assertEqual(ceo["employee_id"], "1000")
        self.assertEqual(ceo["role"], "CEO")
        self.assertEqual(ceo["tier"], "P0_EXECUTIVE")
        self.assertTrue(ceo["is_executive"])

        # CFO Phone (without + prefix)
        cfo = lookup_caller_by_phone("96899000002")
        self.assertIsNotNone(cfo)
        self.assertEqual(cfo["employee_id"], "1010")
        self.assertEqual(cfo["role"], "CFO")
        self.assertEqual(cfo["tier"], "P0_EXECUTIVE")
        self.assertTrue(cfo["is_executive"])

        # Standard employee
        std = lookup_caller_by_phone("+919000000001")
        self.assertIsNotNone(std)
        self.assertEqual(std["tier"], "STANDARD")
        self.assertFalse(std["is_executive"])

    def test_verify_user_with_tier_and_role(self):
        # Ahmed Al Balushi
        res = verify_user("1001", "Ahmed Al Balushi")
        self.assertTrue(res["verified"])
        self.assertEqual(res["tier"], "P1_VIP")
        self.assertEqual(res["role"], "IT Director")

        # Spoken English digits
        res_spoken = verify_user("one zero zero two", "Mohammed Akheel")
        self.assertTrue(res_spoken["verified"])
        self.assertEqual(res_spoken["employee_id"], "1002")
        self.assertEqual(res_spoken["tier"], "STANDARD")

        # Arabic Aliases
        res_arabic = verify_user("1008", "رقية البلوشي")
        self.assertTrue(res_arabic["verified"])
        self.assertEqual(res_arabic["name"], "Ruqaiya Al Balushi")
        self.assertEqual(res_arabic["tier"], "P1_VIP")


if __name__ == "__main__":
    unittest.main()
