from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from unittest.mock import patch

from sessions_app.models import Plan, Session, SuspiciousDevice


class DashboardSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_dashboard_pages_redirect_when_unauthenticated(self):
        response = self.client.get("/iconnect-ops/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/iconnect-ops/login/", response.url)

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
        for path in ["/iconnect-ops/", "/iconnect-ops/revenue/", "/iconnect-ops/sessions/"]:
            page_resp = self.client.get(path)
            self.assertEqual(page_resp.status_code, 200, f"Failed rendering {path}")

    def test_logout_requires_post(self):
        response = self.client.get("/iconnect-ops/logout/")
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
            "/iconnect-ops/login/?next=https://evil.example/phish",
            {
                "username": user.username,
                "password": "admin123",
                "next": "https://evil.example/phish",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/iconnect-ops/")

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
            "/iconnect-ops/login/",
            {"username": "missing", "password": "badpass"},
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/iconnect-ops/login/",
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
            "/iconnect-ops/login/",
            {"username": user.username, "password": "admin123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/iconnect-ops/")
        self.assertTrue(cache_get_mock.called)
        self.assertTrue(cache_delete_mock.called)

    def test_login_with_email(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="email_admin_user",
            email="admin_tech@example.com",
            password="admin123password",
            is_staff=True,
            is_superuser=True,
        )

        response = self.client.post(
            "/iconnect-ops/login/",
            {"username": "admin_tech@example.com", "password": "admin123password"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/iconnect-ops/")

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
            "/iconnect-ops/plans/",
            {"action": "delete", "plan_id": str(plan.id)},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cannot delete this plan")
        self.assertTrue(Plan.objects.filter(id=plan.id).exists())

    def test_export_sessions_csv_requires_admin(self):
        response = self.client.get("/iconnect-ops/sessions/export/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/iconnect-ops/login/", response.url)

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

        response = self.client.get("/iconnect-ops/sessions/export/?period=all")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        content = response.content.decode("utf-8")
        self.assertIn("Session ID,MAC Address,IP Address", content)
        self.assertIn("AA:BB:CC:DD:EE:99", content)

    def test_admin_pause_all_sessions(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="pause_all_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        # Create two active sessions and one expired session
        s1 = Session.objects.create(mac_address="11:11:11:11:11:11", amount_paid=5, duration_minutes_purchased=30, status="active")
        s2 = Session.objects.create(mac_address="22:22:22:22:22:22", amount_paid=10, duration_minutes_purchased=60, status="active")
        s3 = Session.objects.create(mac_address="33:33:33:33:33:33", amount_paid=5, duration_minutes_purchased=30, status="expired")

        resp = self.client.post("/iconnect-ops/sessions/pause-all/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["paused_count"], 2)

        s1.refresh_from_db()
        s2.refresh_from_db()
        s3.refresh_from_db()
        self.assertEqual(s1.status, "paused")
        self.assertEqual(s2.status, "paused")
        self.assertEqual(s3.status, "expired")

    def test_backup_database_download(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="backup_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        resp = self.client.get("/iconnect-ops/settings/backup/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment; filename=", resp["Content-Disposition"])
        self.assertTrue(len(resp.content) > 0)

    def test_admin_user_create_and_delete(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="master_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        # 1. Create a new admin (now uniformly Superadmin)
        create_resp = self.client.post("/iconnect-ops/account/", {
            "action": "create_admin",
            "new_username": "technician_bob",
            "new_email": "bob@tech.com",
            "new_password": "BobPass123!",
        })
        self.assertEqual(create_resp.status_code, 200)
        self.assertTrue(User.objects.filter(username="technician_bob").exists())
        bob = User.objects.get(username="technician_bob")
        self.assertTrue(bob.is_staff)
        self.assertTrue(bob.is_superuser)

        # 2. Cannot delete yourself
        del_self_resp = self.client.post("/iconnect-ops/account/", {
            "action": "delete_admin",
            "target_user_id": str(user.id),
        })
        self.assertEqual(del_self_resp.status_code, 200)
        self.assertTrue(User.objects.filter(id=user.id).exists())

        # 3. Delete technician_bob
        del_bob_resp = self.client.post("/iconnect-ops/account/", {
            "action": "delete_admin",
            "target_user_id": str(bob.id),
        })
        self.assertEqual(del_bob_resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="technician_bob").exists())

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

        response = self.client.get("/iconnect-ops/issues/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Machine took 10 pesos without time")
        self.assertContains(response, "22:33:44:55:66:77")

        # Update status to resolved
        update_resp = self.client.post(
            f"/iconnect-ops/issues/{report.id}/update/",
            {"status": "resolved", "admin_notes": "Added 2 hours manual time"},
        )
        self.assertEqual(update_resp.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.status, "resolved")
        self.assertEqual(report.admin_notes, "Added 2 hours manual time")
        self.assertIsNotNone(report.resolved_at)

        # Delete issue
        del_resp = self.client.post(f"/iconnect-ops/issues/{report.id}/delete/")
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


class AnnouncementManagementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="admin_ann",
            password="adminpassword123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=self.admin.username, password="adminpassword123")

    def test_isp_outage_announcements_do_not_stack_or_show_in_admin(self):
        from dashboard.models import Announcement

        # Create user announcement
        custom_ann = Announcement.objects.create(message="System maintenance at midnight", is_active=True)

        # Create multiple old/stale ISP announcements (as simulated in the screenshot)
        isp_msg = (
            "⚠️ NOTICE: Internet is temporarily interrupted by our ISP. "
            "All user timers have been FROZEN to protect your remaining time! "
            "Your timer will automatically resume as soon as connection is restored."
        )
        Announcement.objects.create(message=isp_msg, is_active=False)
        Announcement.objects.create(message=isp_msg, is_active=False)
        Announcement.objects.create(message=isp_msg, is_active=True)

        # Load announcements management page
        resp = self.client.get("/iconnect-ops/announcements/")
        self.assertEqual(resp.status_code, 200)

        # Stale inactive ISP announcements must be purged
        self.assertEqual(Announcement.objects.filter(message__contains="interrupted by our ISP", is_active=False).count(), 0)

        # Active ISP outage announcement must not be in the announcements list displayed to the owner
        announcements_in_context = resp.context["announcements"]
        self.assertEqual(announcements_in_context.count(), 1)
        self.assertEqual(announcements_in_context.first().id, custom_ann.id)
        self.assertEqual(announcements_in_context.first().message, "System maintenance at midnight")

    def test_isp_restoration_purges_outage_announcements(self):
        from dashboard.models import Announcement
        from sessions_app.tasks import check_internet_status
        from unittest.mock import patch

        isp_msg = "⚠️ NOTICE: Internet is temporarily interrupted by our ISP."
        Announcement.objects.create(message=isp_msg, is_active=True)

        # Mock online check
        with patch("socket.socket") as mock_sock:
            mock_sock.return_value.connect.return_value = None
            res = check_internet_status()
            self.assertIn("ISP restored", res)

        # Verify outage announcement was completely deleted
        self.assertEqual(Announcement.objects.filter(message__contains="interrupted by our ISP").count(), 0)

    def test_paused_session_exceeding_max_hours_is_expired_in_dashboard(self):
        from django.utils import timezone
        from datetime import timedelta
        from sessions_app.models import Session, Plan

        plan_48 = Plan.objects.create(
            name="₱5 48h Plan",
            price=5,
            duration_minutes=120,
            pause_duration_limit=48,
        )

        # Create session paused 55 hours ago
        session = Session.objects.create(
            mac_address="11:22:33:44:55:77",
            status="paused",
            plan=plan_48,
            duration_minutes_purchased=120,
            amount_paid=5,
            time_in=timezone.now() - timedelta(hours=60),
            paused_at=timezone.now() - timedelta(hours=55),
        )

        # Load dashboard sessions page
        resp = self.client.get("/iconnect-ops/sessions/")
        self.assertEqual(resp.status_code, 200)

        # Verify session is now expired
        session.refresh_from_db()
        self.assertEqual(session.status, "expired")
        self.assertIsNotNone(session.time_out)

    def test_stale_session_from_old_plan_exceeding_lifetime_is_expired(self):
        from django.utils import timezone
        from datetime import timedelta
        from sessions_app.models import Session, Plan

        # Old plan with no pause limit
        old_plan = Plan.objects.create(
            name="₱5 90m Plan",
            price=5,
            duration_minutes=90,
            pause_duration_limit=0,
            is_active=False,
        )
        # New active plan with 48h limit
        Plan.objects.create(
            name="₱5 120m Plan",
            price=5,
            duration_minutes=120,
            pause_duration_limit=48,
            is_active=True,
        )

        # Create session started 5 days ago (120 hours ago)
        session = Session.objects.create(
            mac_address="D6:29:15:31:70:EE",
            status="paused",
            plan=old_plan,
            duration_minutes_purchased=90,
            amount_paid=5,
            time_in=timezone.now() - timedelta(days=5),
            paused_at=timezone.now() - timedelta(days=5),
        )

        resp = self.client.get("/iconnect-ops/sessions/")
        self.assertEqual(resp.status_code, 200)

        session.refresh_from_db()
        self.assertEqual(session.status, "expired")
        self.assertEqual(session.time_remaining_seconds, 0)

    def test_admin_creation_rejects_weak_password(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="security_master",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        # Weak password (all lowercase, no special symbol)
        resp = self.client.post("/iconnect-ops/account/", {
            "action": "create_admin",
            "new_username": "weak_user",
            "new_email": "weak@example.com",
            "new_password": "weakpassword1",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="weak_user").exists())

    def test_admin_creation_rejects_invalid_username(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="security_master_2",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        # Username with XSS script injection characters
        resp = self.client.post("/iconnect-ops/account/", {
            "action": "create_admin",
            "new_username": "evil<script>",
            "new_email": "evil@example.com",
            "new_password": "StrongPass123!",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="evil<script>").exists())

    def test_roi_rejects_negative_and_zero_cost(self):
        from dashboard.models import ProjectCost
        User = get_user_model()
        user = User.objects.create_user(
            username="roi_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        # Negative amount
        resp = self.client.post("/iconnect-ops/roi/", {
            "action": "add_cost",
            "description": "Hardware Routers",
            "amount": "-500",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cost amount must be at least 1")
        self.assertFalse(ProjectCost.objects.filter(description="Hardware Routers").exists())

    def test_validators_utility(self):
        from dashboard.validators import (
            validate_password_strength,
            validate_username,
            sanitize_text,
            parse_bounded_int,
            parse_bounded_float,
        )

        # Password rules
        valid, msg = validate_password_strength("weak")
        self.assertFalse(valid)
        valid, msg = validate_password_strength("NoSymbols123")
        self.assertFalse(valid)
        valid, msg = validate_password_strength("NoNumber!Pass")
        self.assertFalse(valid)
        valid, msg = validate_password_strength("validUser123!", username="validUser123!")
        self.assertFalse(valid)
        valid, msg = validate_password_strength("P@ssw0rd2026!")
        self.assertTrue(valid)

        # Username rules
        valid, msg = validate_username("ab")  # too short
        self.assertFalse(valid)
        valid, msg = validate_username("admin; DROP TABLE--")
        self.assertFalse(valid)
        valid, msg = validate_username("valid_admin-01")
        self.assertTrue(valid)

        # Sanitizer
        cleaned = sanitize_text("<script>alert('xss')</script>")
        self.assertNotIn("<script>", cleaned)
        self.assertIn("&lt;script&gt;", cleaned)

        # Bounded parsing
        self.assertEqual(parse_bounded_int("42", 1, 100), 42)
        with self.assertRaises(ValueError):
            parse_bounded_int("-5", 1, 100)
        with self.assertRaises(ValueError):
            parse_bounded_int("9999", 1, 100)
        with self.assertRaises(ValueError):
            parse_bounded_float("-1.5", 0.0, 10.0)

    def test_admin_add_time_to_session(self):
        import json
        from django.utils import timezone
        from sessions_app.models import Session
        User = get_user_model()
        user = User.objects.create_user(
            username="add_time_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        # 1. Create an active session and add time
        session = Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:99",
            status="active",
            duration_minutes_purchased=30,
            amount_paid=5,
        )
        resp = self.client.post(
            f"/iconnect-ops/sessions/{session.id}/add_time/",
            data=json.dumps({"minutes": 45}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.duration_minutes_purchased, 75)
        self.assertEqual(session.status, "active")

        # 2. Add time to an expired session (should reactivate)
        session.status = "expired"
        session.time_out = timezone.now()
        session.save()

        resp = self.client.post(
            f"/iconnect-ops/sessions/{session.id}/add_time/",
            data=json.dumps({"minutes": 60}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, "active")
        self.assertIsNone(session.time_out)

        # 3. Reject negative / zero / out-of-bounds minutes
        resp = self.client.post(
            f"/iconnect-ops/sessions/{session.id}/add_time/",
            data=json.dumps({"minutes": -10}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)






