from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from unittest.mock import patch

from sessions_app.models import Plan, Session, SuspiciousDevice


class DashboardSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_dashboard_pages_redirect_when_unauthenticated(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/login/", response.url)

    def test_dashboard_api_requires_authentication(self):
        endpoints = [
            "/api/announcements/",
            "/api/dashboard/stats/",
            "/api/dashboard/heatmap/",
            "/api/dashboard/revenue/",
        ]

        for endpoint in endpoints:
            response = self.client.get(endpoint)
            self.assertIn(response.status_code, (401, 403))

    def test_dashboard_api_allows_staff(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="dashboard_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        logged_in = self.client.login(username=user.username, password="admin123")
        self.assertTrue(logged_in)

        response = self.client.get("/api/dashboard/stats/")
        self.assertEqual(response.status_code, 200)

        # Test HTML template rendering for main pages
        for path in ["/dashboard/", "/dashboard/revenue/", "/dashboard/sessions/"]:
            page_resp = self.client.get(path)
            self.assertEqual(page_resp.status_code, 200, f"Failed rendering {path}")

    def test_logout_requires_post(self):
        response = self.client.get("/dashboard/logout/")
        self.assertEqual(response.status_code, 405)

    def test_login_rejects_external_next_redirect(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="redirect_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )

        response = self.client.post(
            "/dashboard/login/?next=https://evil.example/phish",
            {
                "username": user.username,
                "password": "admin123",
                "next": "https://evil.example/phish",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/dashboard/")

    @override_settings(
        PISONET_LOGIN_MAX_ATTEMPTS=1,
        PISONET_LOGIN_WINDOW_SECONDS=300,
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "dashboard-login-rate-limit-tests",
            }
        },
    )
    def test_login_rate_limit_triggers(self):
        first = self.client.post(
            "/dashboard/login/",
            {"username": "missing", "password": "badpass"},
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/dashboard/login/",
            {"username": "missing", "password": "badpass"},
        )
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "Too many login attempts")

    @patch("dashboard.views.cache.delete", side_effect=Exception("cache unavailable"))
    @patch("dashboard.views.cache.get", side_effect=Exception("cache unavailable"))
    def test_login_does_not_500_when_cache_is_unavailable(self, cache_get_mock, cache_delete_mock):
        User = get_user_model()
        user = User.objects.create_user(
            username="cache_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )

        response = self.client.post(
            "/dashboard/login/",
            {"username": user.username, "password": "admin123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/dashboard/")
        self.assertTrue(cache_get_mock.called)
        self.assertTrue(cache_delete_mock.called)

    def test_plan_delete_shows_error_when_plan_is_in_use(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="plans_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        logged_in = self.client.login(username=user.username, password="admin123")
        self.assertTrue(logged_in)

        plan = Plan.objects.create(name="P5", price=5, duration_minutes=30, is_active=True)
        Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:11",
            plan=plan,
            duration_minutes_purchased=30,
            remaining_minutes=30,
            amount_paid=5,
            status="active",
        )

        response = self.client.post(
            "/dashboard/plans/",
            {"action": "delete", "plan_id": str(plan.id)},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cannot delete this plan")
        self.assertTrue(Plan.objects.filter(id=plan.id).exists())

    def test_export_sessions_csv_requires_admin(self):
        response = self.client.get("/dashboard/sessions/export/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/login/", response.url)

    def test_export_sessions_csv_returns_csv_file(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="export_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        plan = Plan.objects.create(name="P5", price=5, duration_minutes=30, is_active=True)
        Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:99",
            plan=plan,
            duration_minutes_purchased=30,
            amount_paid=5,
            status="active",
        )

        response = self.client.get("/dashboard/sessions/export/?period=all")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        content = response.content.decode("utf-8")
        self.assertIn("Session ID,MAC Address,IP Address", content)
        self.assertIn("AA:BB:CC:DD:EE:99", content)

    def test_issues_view_and_management(self):
        from dashboard.models import IssueReport
        User = get_user_model()
        user = User.objects.create_user(
            username="issue_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        report = IssueReport.objects.create(
            mac_address="22:33:44:55:66:77",
            contact_info="09991234567",
            category="coin_stuck",
            message="Machine took 10 pesos without time",
            status="pending",
        )

        response = self.client.get("/dashboard/issues/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Machine took 10 pesos without time")
        self.assertContains(response, "22:33:44:55:66:77")

        # Update status to resolved
        update_resp = self.client.post(
            f"/dashboard/issues/{report.id}/update/",
            {"status": "resolved", "admin_notes": "Added 2 hours manual time"},
        )
        self.assertEqual(update_resp.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.status, "resolved")
        self.assertEqual(report.admin_notes, "Added 2 hours manual time")
        self.assertIsNotNone(report.resolved_at)

        # Delete issue
        del_resp = self.client.post(f"/dashboard/issues/{report.id}/delete/")
        self.assertEqual(del_resp.status_code, 302)
        self.assertFalse(IssueReport.objects.filter(id=report.id).exists())

    def test_realtime_apis(self):
        from decimal import Decimal
        from sessions_app.models import CoinEvent
        User = get_user_model()
        user = User.objects.create_user(
            username="live_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        plan = Plan.objects.create(
            name="1 Hour Plan",
            price=Decimal("10.00"),
            duration_minutes=60,
            speed_limit=Decimal("10.0"),
            speed_limit_upload=Decimal("5.0"),
            is_active=True,
        )

        session = Session.objects.create(
            device_name="Test Phone",
            mac_address="AA:BB:CC:DD:EE:FF",
            ip_address="10.10.10.50",
            plan=plan,
            amount_paid=Decimal("10.00"),
            duration_minutes_purchased=60,
            status="active",
        )

        CoinEvent.objects.create(
            session=session,
            amount=10,
            denomination=10,
        )

        # 1. Test dashboard stats API with recent_sessions
        resp = self.client.get("/api/dashboard/stats/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("recent_sessions", data)
        self.assertGreaterEqual(len(data["recent_sessions"]), 1)
        self.assertEqual(data["recent_sessions"][0]["mac_address"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(data["recent_sessions"][0]["plan_name"], "1 Hour Plan")

        # 2. Test revenue live API
        rev_resp = self.client.get("/api/dashboard/revenue/live/?period=today")
        self.assertEqual(rev_resp.status_code, 200)
        rev_data = rev_resp.json()
        self.assertIn("total_sales", rev_data)
        self.assertIn("sessions", rev_data)
        self.assertEqual(rev_data["total_sessions"], 1)
        self.assertEqual(rev_data["sessions"][0]["mac_address"], "AA:BB:CC:DD:EE:FF")

        # 3. Test sessions live API
        sess_resp = self.client.get("/api/dashboard/sessions/live/?period=today")
        self.assertEqual(sess_resp.status_code, 200)
        sess_data = sess_resp.json()
        self.assertIn("connected_users", sess_data)
        self.assertIn("sessions", sess_data)
        self.assertEqual(sess_data["connected_users"], 1)
        self.assertEqual(sess_data["sessions"][0]["mac_address"], "AA:BB:CC:DD:EE:FF")

