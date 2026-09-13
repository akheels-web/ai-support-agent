import unittest
from app.openai_realtime_bridge import normalize_text_for_search, search_knowledge_base


class TestKnowledgeBaseSearch(unittest.TestCase):

    def test_text_normalization(self):
        # Arabic diacritics / alef forms
        norm = normalize_text_for_search("إعادة تعيين كلمة المرور!")
        self.assertEqual(norm, "اعاده تعيين كلمه المرور")

        # English punctuation
        norm_en = normalize_text_for_search("Wi-Fi / Network Issue?")
        self.assertEqual(norm_en, "wi fi network issue")

    def test_search_wifi(self):
        res1 = search_knowledge_base("wifi not connecting")
        self.assertTrue(res1["found"])
        self.assertEqual(res1["playbook_id"], "wifi_issue")

        res2 = search_knowledge_base("مشكلة في شبكة الواي فاي")
        self.assertTrue(res2["found"])
        self.assertEqual(res2["playbook_id"], "wifi_issue")

    def test_search_printer(self):
        res1 = search_knowledge_base("printer is jammed cannot print")
        self.assertTrue(res1["found"])
        self.assertEqual(res1["playbook_id"], "printer_issue")

        res2 = search_knowledge_base("مشكلة في الطباعة وحبر الطابعة")
        self.assertTrue(res2["found"])
        self.assertEqual(res2["playbook_id"], "printer_issue")

    def test_search_password_and_lockout(self):
        res1 = search_knowledge_base("forgot my password")
        self.assertTrue(res1["found"])
        self.assertEqual(res1["playbook_id"], "password_reset")

        res2 = search_knowledge_base("نسيت كلمة المرور")
        self.assertTrue(res2["found"])
        self.assertEqual(res2["playbook_id"], "password_reset")

        res3 = search_knowledge_base("my account is locked out")
        self.assertTrue(res3["found"])
        self.assertEqual(res3["playbook_id"], "account_locked")

        res4 = search_knowledge_base("حسابي مقفل")
        self.assertTrue(res4["found"])
        self.assertEqual(res4["playbook_id"], "account_locked")

    def test_search_slow_pc_and_outlook(self):
        res1 = search_knowledge_base("laptop is very slow and freezing")
        self.assertTrue(res1["found"])
        self.assertEqual(res1["playbook_id"], "slow_computer")

        res2 = search_knowledge_base("الجهاز بطيء جدا ومعلق")
        self.assertTrue(res2["found"])
        self.assertEqual(res2["playbook_id"], "slow_computer")

        res3 = search_knowledge_base("outlook send receive error")
        self.assertTrue(res3["found"])
        self.assertEqual(res3["playbook_id"], "outlook_issue")

        res4 = search_knowledge_base("البريد الالكتروني والايميل")
        self.assertTrue(res4["found"])
        self.assertEqual(res4["playbook_id"], "outlook_issue")

    def test_search_vpn(self):
        res1 = search_knowledge_base("cannot connect to vpn gateway from home")
        self.assertTrue(res1["found"])
        self.assertEqual(res1["playbook_id"], "vpn_issue")

        res2 = search_knowledge_base("الفي بي ان لا يعمل")
        self.assertTrue(res2["found"])
        self.assertEqual(res2["playbook_id"], "vpn_issue")


if __name__ == "__main__":
    unittest.main()
