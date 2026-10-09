import unittest
import time
from fastapi.testclient import TestClient
from dashboard.app import app, create_session, hash_password, db, COOKIE_NAME


class TestDashboardRoutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, follow_redirects=False)
        # Ensure test admin user exists in DB
        conn = db()
        conn.execute(
            """
            INSERT INTO users(username, password_hash, role, active, created_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role=excluded.role
            """,
            ("test_admin", hash_password("admin_pass123"), "admin", int(time.time())),
        )
        conn.commit()
        conn.close()

        # Create session token for test_admin
        cls.admin_token = create_session("test_admin")

    def setUp(self):
        self.client = TestClient(app, follow_redirects=False)

    def test_unauthenticated_redirect(self):
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers.get("Location"), "/login")

    def test_login_page_renders_shadcn(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Operations Login", resp.text)
        self.assertIn("csrf_token", resp.text)
        self.assertIn("/static/css/shadcn.css", resp.text)

    def test_authenticated_dashboard_overview(self):
        self.client.cookies.set(COOKIE_NAME, self.admin_token)
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Overview", resp.text)
        self.assertIn("kpi-total-calls", resp.text)
        self.assertIn("volumeTrendChart", resp.text)
        self.assertIn("deflectionDoughnutChart", resp.text)

    def test_api_dashboard_stats(self):
        self.client.cookies.set(COOKIE_NAME, self.admin_token)
        resp = self.client.get("/api/dashboard/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total_calls", data)
        self.assertIn("deflection_rate", data)
        self.assertIn("emergency_calls", data)
        self.assertIn("vip_calls", data)

    def test_api_dashboard_chart_data(self):
        self.client.cookies.set(COOKIE_NAME, self.admin_token)
        resp = self.client.get("/api/dashboard/chart-data")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("volume_trend", data)
        self.assertIn("deflection_breakdown", data)
        self.assertIn("queue_distribution", data)

    def test_calls_table_and_csv_export(self):
        self.client.cookies.set(COOKIE_NAME, self.admin_token)
        resp = self.client.get("/calls")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Call History & Telemetry", resp.text)
        self.assertIn("table-search-input", resp.text)

        # Test CSV export
        csv_resp = self.client.get("/calls?export=csv")
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp.headers.get("content-type", ""))
        self.assertIn("Call ID,Caller Number", csv_resp.text)

    def test_active_calls_and_api(self):
        self.client.cookies.set(COOKIE_NAME, self.admin_token)
        resp = self.client.get("/active-calls")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Active Live Calls", resp.text)

        api_resp = self.client.get("/api/active-calls/data")
        self.assertEqual(api_resp.status_code, 200)
        self.assertIsInstance(api_resp.json(), list)

    def test_admin_only_routes(self):
        self.client.cookies.set(COOKIE_NAME, self.admin_token)
        for route in ["/health", "/security-events", "/settings", "/users"]:
            resp = self.client.get(route)
            self.assertEqual(resp.status_code, 200, f"Failed for route {route}")
        # /prompts redirects to /settings
        prompts_resp = self.client.get("/prompts")
        self.assertIn(prompts_resp.status_code, (200, 302))

    def test_security_headers_present(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("Content-Security-Policy", resp.headers)

    def test_setup_locked_when_admin_exists(self):
        # Admin already exists, so /setup must redirect to /login
        resp = self.client.get("/setup")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers.get("Location"), "/login")

    def test_session_invalidation_helper(self):
        from dashboard.app import invalidate_user_sessions, get_session_user
        token = create_session("test_temp_user")
        self.assertEqual(get_session_user(token), "test_temp_user")
        invalidate_user_sessions("test_temp_user")
        self.assertIsNone(get_session_user(token))

    def test_audit_log_hash_chaining_and_integrity(self):
        from dashboard.app import audit, verify_audit_trail
        audit("security_test_admin", "user_login", "auth", "127.0.0.1")
        audit("security_test_admin", "update_setting", "config", "timeout")
        audit("security_test_admin", "logout", "auth", "127.0.0.1")

        result = verify_audit_trail()
        self.assertTrue(result["valid"], f"Audit trail failed verification: {result}")
        self.assertGreaterEqual(result["verified_records"], 3)


if __name__ == "__main__":
    unittest.main()
