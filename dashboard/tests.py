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

        response_security = self.client.get("/dashboard/security/")
        self.assertEqual(response_security.status_code, 302)
        self.assertIn("/dashboard/login/", response_security.url)

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

    @patch("dashboard.views.iptables.block_device", return_value=True)
    def test_security_page_block_action(self, block_device_mock):
        User = get_user_model()
        user = User.objects.create_user(
            username="security_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        logged_in = self.client.login(username=user.username, password="admin123")
        self.assertTrue(logged_in)

        incident = SuspiciousDevice.objects.create(
            mac_address="AA:BB:CC:DD:EE:01",
            last_ip_address="10.0.0.55",
            reason="mac_ip_conflict_status",
        )

        response = self.client.post(
            "/dashboard/security/",
            {
                "action": "block",
                "incident_id": incident.id,
            },
        )
        self.assertEqual(response.status_code, 200)

        incident.refresh_from_db()
        self.assertEqual(incident.status, SuspiciousDevice.STATUS_BLOCKED)
        self.assertTrue(incident.is_blocked)
        block_device_mock.assert_called_once_with(incident.mac_address)

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
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cannot delete this plan because it is already used by existing sessions")
        self.assertTrue(Plan.objects.filter(id=plan.id).exists())
