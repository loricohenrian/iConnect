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
            remaining_minutes=0,
            amount_paid=5,
            status="expired",
        )

        response = self.client.post(
            "/dashboard/plans/",
            {"action": "delete", "plan_id": str(plan.id)},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cannot delete this plan because it is already used by existing sessions")
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

