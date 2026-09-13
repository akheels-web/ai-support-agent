import os
import tempfile
import unittest
from pathlib import Path

import app.db as app_db
from app.openai_realtime_bridge import (
    invalidate_knowledge_cache,
    search_knowledge_base,
    get_active_knowledge_articles,
)


class TestKnowledgeBaseManagement(unittest.TestCase):

    def setUp(self):
        # Ensure fresh DB state for test
        app_db.init_db()
        app_db.seed_knowledge_articles_if_empty()
        invalidate_knowledge_cache()

    def test_articles_seeded(self):
        articles = app_db.list_knowledge_articles(active_only=False)
        self.assertGreaterEqual(len(articles), 10)

        # Check standard seeded article attributes
        wifi = app_db.get_knowledge_article("wifi_issue")
        self.assertIsNotNone(wifi)
        self.assertEqual(wifi["category"], "Network & Connectivity")
        self.assertIn("wifi", wifi["keywords_en"].lower())
        self.assertIn("واي فاي", wifi["keywords_ar"])
        self.assertEqual(wifi["active"], 1)

    def test_save_and_disk_sync(self):
        test_id = "test_oracle_cloud"
        test_title = "Oracle Cloud Financials Troubleshooting"
        test_category = "Enterprise Systems"
        test_kw_en = "oracle, erp, financials, gl ledger, journal voucher"
        test_kw_ar = "اوراكل, النظام المالي, سند قيد, القيود المالية"
        test_content = "# Oracle Cloud\n1. Clear browser cookies.\n2. Re-authenticate via SSO."

        success = app_db.save_knowledge_article(
            article_id=test_id,
            title=test_title,
            category=test_category,
            keywords_en=test_kw_en,
            keywords_ar=test_kw_ar,
            content=test_content,
            active=1,
            created_by="admin_test",
        )
        self.assertTrue(success)

        # Verify DB entry
        art = app_db.get_knowledge_article(test_id)
        self.assertIsNotNone(art)
        self.assertEqual(art["title"], test_title)
        self.assertEqual(art["category"], test_category)

        # Verify disk sync
        disk_file = app_db.BASE_DIR / "knowledge_base" / f"{test_id}.md"
        self.assertTrue(disk_file.exists())
        self.assertIn("Clear browser cookies", disk_file.read_text(encoding="utf-8"))

        # Clean up disk file
        try:
            disk_file.unlink()
        except Exception:
            pass
        app_db.delete_knowledge_article(test_id)

    def test_live_bridge_reflection_and_toggle(self):
        test_id = "pulse_secure_vpn"
        test_title = "Pulse Secure VPN Branch Access"
        test_category = "Network & Connectivity"
        test_kw_en = "pulse secure, pulse vpn, branch tunnel, ssl vpn"
        test_kw_ar = "بولس سيكيور, في بي ان الفروع, نفق الاتصال"
        test_content = "# Pulse Secure\n1. Ensure Pulse Secure Client is running.\n2. Connect to vpn.nationalfinance.om."

        # Save to DB
        app_db.save_knowledge_article(
            article_id=test_id,
            title=test_title,
            category=test_category,
            keywords_en=test_kw_en,
            keywords_ar=test_kw_ar,
            content=test_content,
            active=1,
            created_by="admin_test",
        )

        # Invalidate in-memory cache to simulate instant reflection
        invalidate_knowledge_cache()

        # Test search discovery in English
        res_en = search_knowledge_base("my pulse vpn is disconnected")
        self.assertTrue(res_en.get("found"))
        self.assertEqual(res_en.get("playbook_id"), test_id)
        self.assertEqual(res_en.get("title"), test_title)

        # Test search discovery in Arabic
        res_ar = search_knowledge_base("مشكلة في بولس سيكيور في بي ان")
        self.assertTrue(res_ar.get("found"))
        self.assertEqual(res_ar.get("playbook_id"), test_id)

        # Test toggle to inactive
        new_active = app_db.toggle_knowledge_article(test_id)
        self.assertEqual(new_active, 0)
        invalidate_knowledge_cache()

        # Inactive article must NOT be returned as active match
        res_inactive = search_knowledge_base("pulse secure branch tunnel")
        if res_inactive.get("found"):
            self.assertNotEqual(res_inactive.get("playbook_id"), test_id)

        # Clean up
        disk_file = app_db.BASE_DIR / "knowledge_base" / f"{test_id}.md"
        if disk_file.exists():
            disk_file.unlink()
        app_db.delete_knowledge_article(test_id)
        invalidate_knowledge_cache()


if __name__ == "__main__":
    unittest.main()
