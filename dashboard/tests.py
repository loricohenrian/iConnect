from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from unittest.mock import patch

from sessions_app.models import Plan, Session, SuspiciousDevice, CoinEvent, SessionGroup
from dashboard.models import RevenueGoal, DailyRevenueSummary
from django.core.cache import cache


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
        from datetime import timedelta
        from decimal import Decimal
        from django.utils import timezone
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
        # Old paused session from 6 days ago should NOT appear under "today", but should appear under "week"
        old_paused = Session.objects.create(
            mac_address="11:22:33:44:55:66",
            status="paused",
            time_in=timezone.now() - timedelta(days=6),
            paused_at=timezone.now() - timedelta(days=6),
            duration_minutes_purchased=60,
            amount_paid=10,
        )

        sess_resp = self.client.get("/api/dashboard/sessions/live/?period=today")
        self.assertEqual(sess_resp.status_code, 200)
        sess_data = sess_resp.json()
        self.assertIn("connected_users", sess_data)
        self.assertIn("sessions", sess_data)
        self.assertEqual(sess_data["connected_users"], 1)
        self.assertEqual(len(sess_data["sessions"]), 1)
        self.assertEqual(sess_data["sessions"][0]["mac_address"], "AA:BB:CC:DD:EE:FF")

        # But under week filter, the old paused session appears
        week_resp = self.client.get("/api/dashboard/sessions/live/?period=week")
        self.assertEqual(week_resp.status_code, 200)
        week_data = week_resp.json()
        macs = [s["mac_address"] for s in week_data["sessions"]]
        self.assertIn("11:22:33:44:55:66", macs)


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
        from decimal import Decimal
        from django.utils import timezone
        from sessions_app.models import Session, Plan
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

        # 4. Add time with a plan preset
        plan = Plan.objects.create(
            name="1 Hour Plan",
            price=10,
            duration_minutes=60,
            speed_limit=Decimal("8.0"),
            speed_limit_upload=Decimal("4.0"),
            is_active=True,
        )
        resp = self.client.post(
            f"/iconnect-ops/sessions/{session.id}/add_time/",
            data=json.dumps({"minutes": 60, "plan_id": plan.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.plan_id, plan.id)

        # 5. Add time with custom speed limit overrides
        resp = self.client.post(
            f"/iconnect-ops/sessions/{session.id}/add_time/",
            data=json.dumps({"minutes": 30, "speed_limit": 15.5, "speed_limit_upload": 7.5}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        # 6. Reject invalid speed limit bounds
        resp = self.client.post(
            f"/iconnect-ops/sessions/{session.id}/add_time/",
            data=json.dumps({"minutes": 30, "speed_limit": -5.0}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_save_global_settings_success(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="settings_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        resp = self.client.post("/iconnect-ops/settings/", {
            "isp_download_speed": 150,
            "isp_upload_speed": 100,
            "enable_dark_mode": "on",
            "max_concurrent_sessions": 25,
            "global_pause_limit_hours": 12,
            "auto_pause_timeout_seconds": 300,
            "insert_coin_countdown_seconds": 120,
            "spin_cost_points": 10,
            "daily_spin_limit": 3,
            "points_per_streak_day": 5,
            "telegram_bot_token": "123456789:ABCdef-gh1234_xyz1234567890ABC",
            "telegram_admin_chat_id": "6261306648",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Settings updated successfully.")


class AnalyticsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_analytics_new_vs_returning_devices_breakdown(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="analytics_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        plan = Plan.objects.create(name="1hr Plan", price=10, duration_minutes=60)
        # Device 1: 2 sessions (returning)
        Session.objects.create(mac_address="AA:BB:CC:DD:EE:01", ip_address="10.0.0.2", plan=plan, amount_paid=10, duration_minutes_purchased=60, status="active")
        Session.objects.create(mac_address="AA:BB:CC:DD:EE:01", ip_address="10.0.0.2", plan=plan, amount_paid=10, duration_minutes_purchased=60, status="expired")
        # Device 2: 1 session (first-time)
        Session.objects.create(mac_address="AA:BB:CC:DD:EE:02", ip_address="10.0.0.3", plan=plan, amount_paid=10, duration_minutes_purchased=60, status="expired")

        resp = self.client.get("/iconnect-ops/analytics/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["unique_devices"], 2)
        self.assertEqual(resp.context["returning_devices"], 1)
        self.assertEqual(resp.context["first_time_devices"], 1)
        self.assertEqual(resp.context["retention_rate"], 50.0)
        self.assertContains(resp, "1 returning · 1 first-time")


class RoiTrackerEnhancementTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_roi_historical_trend_and_small_sample_caveat(self):
        from dashboard.models import ProjectCost, OperatingExpense
        from django.utils import timezone
        from datetime import timedelta
        import json

        User = get_user_model()
        user = User.objects.create_user(
            username="roi_test_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        now = timezone.now()
        # Day 1: ProjectCost added 3 days ago (4 days operating: 3 days ago, 2 days ago, 1 day ago, today)
        cost = ProjectCost.objects.create(
            description="Orange Pi Zero 3",
            amount=2500,
            date_added=now - timedelta(days=3)
        )
        # Recurring operating expense: 300 / month = 10 / day
        OperatingExpense.objects.create(
            name="ISP Bill",
            amount=300,
            period="monthly"
        )
        # Add CoinEvents:
        # Day 1 (3 days ago): 50
        CoinEvent.objects.create(amount=50, denomination=10, timestamp=now - timedelta(days=3))
        # Day 2 (2 days ago): 100
        CoinEvent.objects.create(amount=100, denomination=10, timestamp=now - timedelta(days=2))
        # Day 4 (today): 150
        CoinEvent.objects.create(amount=150, denomination=10, timestamp=now)

        resp = self.client.get("/iconnect-ops/roi/")
        self.assertEqual(resp.status_code, 200)

        # Verify days operating
        self.assertEqual(resp.context["days_operating"], 4)

        # Verify caveat in rendered HTML
        self.assertContains(resp, "Estimates based on <strong>4 days</strong> of data — accuracy improves with more operating history.")

        # Verify chart canvas in HTML
        self.assertContains(resp, 'id="profit-trend-chart"')

        # Verify profit trend data in context
        labels = json.loads(resp.context["profit_trend_labels"])
        values = json.loads(resp.context["profit_trend_values"])
        self.assertEqual(len(labels), 4)
        self.assertEqual(len(values), 4)
        # Total coins = 300. Total expenses = 4 * 10 = 40. Final net profit = 260.
        self.assertEqual(resp.context["net_profit"], 260.0)
        self.assertEqual(values[-1], 260.0)


class ReportsEnhancementTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_reports_itemized_expenses_and_plan_percentages(self):
        from dashboard.models import ProjectCost, OperatingExpense
        from django.utils import timezone
        from datetime import timedelta

        User = get_user_model()
        user = User.objects.create_user(
            username="reports_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=user.username, password="admin123")

        now = timezone.now()
        # Create ProjectCost and OperatingExpense
        ProjectCost.objects.create(description="Hardware Router", amount=1800, date_added=now)
        OperatingExpense.objects.create(name="PLDT Fiber", amount=1500, period="monthly", date_added=now)

        # Create plans and sessions
        p1 = Plan.objects.create(name="1hr Plan", price=10, duration_minutes=60)
        p2 = Plan.objects.create(name="3hr Plan", price=25, duration_minutes=180)

        # 3 sessions for p1 (₱30)
        for _ in range(3):
            Session.objects.create(mac_address="AA:BB:CC:DD:EE:01", ip_address="10.0.0.2", plan=p1, amount_paid=10, duration_minutes_purchased=60, status="expired", time_in=now)
        # 1 session for p2 (₱25)
        Session.objects.create(mac_address="AA:BB:CC:DD:EE:02", ip_address="10.0.0.3", plan=p2, amount_paid=25, duration_minutes_purchased=180, status="expired", time_in=now)

        resp = self.client.get("/iconnect-ops/reports/")
        self.assertEqual(resp.status_code, 200)

        # Check itemized expenses in context
        itemized = resp.context["itemized_expenses"]
        self.assertEqual(len(itemized), 2)
        names = [item["name"] for item in itemized]
        self.assertIn("PLDT Fiber", names)
        self.assertIn("Hardware Router", names)

        # Check % of Total column and itemized table in HTML
        self.assertContains(resp, "% of Total")
        self.assertContains(resp, "Itemized Operating & Capital Expenses")
        self.assertContains(resp, "PLDT Fiber")
        self.assertContains(resp, "Hardware Router")

        # Total revenue = 55. Total count = 4.
        # p1: 30 / 55 = 54.5% rev, 3 / 4 = 75.0% vol
        # p2: 25 / 55 = 45.5% rev, 1 / 4 = 25.0% vol
        top_plans = resp.context["top_plans"]
        p1_data = next(p for p in top_plans if p["plan__name"] == "1hr Plan")
        self.assertEqual(p1_data["pct_revenue"], 54.5)
        self.assertEqual(p1_data["pct_count"], 75.0)

        self.assertContains(resp, "54.5%")
        self.assertContains(resp, "(75.0% vol)")


class SupportTicketHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.admin = User.objects.create_superuser("ticket_admin", "admin@test.com", "pass1234")
        self.client.login(username="ticket_admin", password="pass1234")
        from dashboard.models import IssueReport
        self.report = IssueReport.objects.create(
            mac_address="11:22:33:44:55:66",
            contact_info="09123456789",
            category="coin_stuck",
            message="Coin slot ate ₱5 coin_test *bold*",
            status="pending"
        )

    def test_search_by_ticket_id(self):
        # Search with '#<id>'
        resp = self.client.get(f"/iconnect-ops/issues/?q=%23{self.report.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.report, resp.context["reports"])

        # Search with raw digit id
        resp = self.client.get(f"/iconnect-ops/issues/?q={self.report.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.report, resp.context["reports"])

        # Search with non-existent id
        resp = self.client.get("/iconnect-ops/issues/?q=%2399999")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(self.report, resp.context["reports"])

    def test_update_issue_status_and_notes(self):
        resp = self.client.post(
            f"/iconnect-ops/issues/{self.report.id}/update/",
            {"status": "resolved", "admin_notes": "Manually refunded ₱5 to customer."}
        )
        self.assertEqual(resp.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "resolved")
        self.assertEqual(self.report.admin_notes, "Manually refunded ₱5 to customer.")
        self.assertIsNotNone(self.report.resolved_at)

    def test_open_redirect_protection(self):
        # Malicious referer should fallback to safe internal URL
        resp = self.client.post(
            f"/iconnect-ops/issues/{self.report.id}/update/",
            {"status": "pending"},
            HTTP_REFERER="https://malicious-phishing.com/steal"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/iconnect-ops/issues/")

        # Safe internal referer should be honored
        resp = self.client.post(
            f"/iconnect-ops/issues/{self.report.id}/update/",
            {"status": "pending"},
            HTTP_REFERER="http://testserver/iconnect-ops/issues/?status=pending"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "http://testserver/iconnect-ops/issues/?status=pending")

    def test_telegram_escape_markdown(self):
        from dashboard.telegram_bot import escape_markdown
        text = "Hello_world *test* `code` [link]"
        escaped = escape_markdown(text)
        self.assertEqual(escaped, r"Hello\_world \*test\* \`code\` \[link]")

    def test_api_report_issue_deduplication(self):
        from django.core.cache import cache
        cache.clear()
        client = APIClient()

        payload = {
            "mac": "11:22:33:44:55:66",
            "category": "coin_stuck",
            "message": "Double submit test message",
            "contact": "09123456789"
        }
        # First submission
        resp1 = client.post("/api/report-issue/", payload)
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1.json().get("status"), "success")

        # Immediate duplicate submission
        resp2 = client.post("/api/report-issue/", payload)
        self.assertEqual(resp2.status_code, 200)
        self.assertIn("already been received", resp2.json().get("message", ""))


class SecuritySystemHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.admin = User.objects.create_superuser("sec_admin", "sec@test.com", "pass1234")
        self.client.login(username="sec_admin", password="pass1234")
        self.test_mac = "EE:AA:BB:CC:DD:11"
        self.incident = SuspiciousDevice.objects.create(
            mac_address=self.test_mac,
            last_ip_address="10.0.0.50",
            reason="MAC Spoofing Suspected",
            evidence="Flapped rapidly between 10.0.0.50 and 10.0.0.51",
            status=SuspiciousDevice.STATUS_BLOCKED,
            is_blocked=True,
        )

    @patch("sessions_app.iptables.allow_device")
    @patch("sessions_app.iptables.block_device")
    def test_unblock_without_active_session_does_not_grant_free_internet(self, mock_block, mock_allow):
        # Device has NO active session
        resp = self.client.post("/iconnect-ops/security/", {
            "action": "unblock",
            "incident_id": self.incident.id,
        })
        self.assertEqual(resp.status_code, 302)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, SuspiciousDevice.STATUS_CLEARED)
        self.assertFalse(self.incident.is_blocked)

        # Critical: allow_device MUST NOT be called! Device stays in captive redirect!
        mock_allow.assert_not_called()
        mock_block.assert_called_with(self.test_mac)

    @patch("sessions_app.iptables.allow_device")
    def test_unblock_with_active_session_restores_firewall(self, mock_allow):
        plan = Plan.objects.create(name="1 Hour", price=10, duration_minutes=60, speed_limit=5.0)
        Session.objects.create(
            mac_address=self.test_mac,
            plan=plan,
            duration_minutes_purchased=60,
            amount_paid=10,
            status="active",
        )
        resp = self.client.post("/iconnect-ops/security/", {
            "action": "unblock",
            "incident_id": self.incident.id,
        })
        self.assertEqual(resp.status_code, 302)
        mock_allow.assert_called_once()

    @patch("sessions_app.iptables.block_device")
    def test_block_action_terminates_active_sessions(self, mock_block):
        inc = SuspiciousDevice.objects.create(
            mac_address="22:33:44:55:66:77",
            status=SuspiciousDevice.STATUS_NEW,
            is_blocked=False,
        )
        plan = Plan.objects.create(name="1 Hour", price=10, duration_minutes=60)
        s1 = Session.objects.create(
            mac_address="22:33:44:55:66:77",
            plan=plan,
            duration_minutes_purchased=60,
            amount_paid=10,
            status="active",
        )
        s2 = Session.objects.create(
            mac_address="22:33:44:55:66:77",
            plan=plan,
            duration_minutes_purchased=60,
            amount_paid=10,
            status="paused",
        )

        resp = self.client.post("/iconnect-ops/security/", {
            "action": "block",
            "incident_id": inc.id,
        })
        self.assertEqual(resp.status_code, 302)
        inc.refresh_from_db()
        self.assertEqual(inc.status, SuspiciousDevice.STATUS_BLOCKED)
        self.assertTrue(inc.is_blocked)

        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.status, "expired")
        self.assertEqual(s2.status, "expired")
        self.assertIsNotNone(s1.time_out)
        self.assertIsNotNone(s2.time_out)

    @patch("sessions_app.iptables.block_device")
    def test_manual_block_action(self, mock_block):
        new_mac = "AA:BB:CC:99:88:77"
        resp = self.client.post("/iconnect-ops/security/", {
            "action": "manual_block",
            "mac_address": new_mac,
            "reason": "Payment Evasion",
            "evidence": "Observed bypassing coin drop",
        })
        self.assertEqual(resp.status_code, 302)
        inc = SuspiciousDevice.objects.filter(mac_address=new_mac).first()
        self.assertIsNotNone(inc)
        self.assertEqual(inc.status, SuspiciousDevice.STATUS_BLOCKED)
        self.assertTrue(inc.is_blocked)
        self.assertEqual(inc.reason, "Payment Evasion")
        mock_block.assert_called_with(new_mac)

    def test_delete_action(self):
        resp = self.client.post("/iconnect-ops/security/", {
            "action": "delete",
            "incident_id": self.incident.id,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(SuspiciousDevice.objects.filter(id=self.incident.id).exists())

    def test_spin_wheel_rejects_blocked_device(self):
        from dashboard.models import SystemSettings
        from sessions_app.models import DeviceProfile
        sys_settings = SystemSettings.get_settings()
        sys_settings.enable_spin_wheel = True
        sys_settings.save()

        DeviceProfile.objects.create(mac_address=self.test_mac, points=100)

        # Anonymous client representing customer device
        anon_client = APIClient()
        with patch("portal.views._get_mac_address", return_value=self.test_mac):
            resp = anon_client.post("/api/execute_spin/")
            self.assertEqual(resp.status_code, 403)
            self.assertIn("blocked", resp.json().get("message", ""))

            # Spin data should also report blocked
            data_resp = anon_client.get("/api/spin-data/")
            self.assertEqual(data_resp.status_code, 200)
            self.assertTrue(data_resp.json().get("is_blocked"))
            self.assertFalse(data_resp.json().get("enabled"))

    def test_spin_wheel_successful_execution(self):
        from dashboard.models import SystemSettings
        from sessions_app.models import DeviceProfile, SpinPrize
        sys_settings = SystemSettings.get_settings()
        sys_settings.enable_spin_wheel = True
        sys_settings.spin_cost_points = 10
        sys_settings.daily_spin_limit = 5
        sys_settings.save()

        # Create active prizes
        SpinPrize.objects.create(
            name="15 Mins Free",
            minutes_reward=15,
            probability_weight=50,
            is_active=True
        )
        SpinPrize.objects.create(
            name="Try Again",
            minutes_reward=0,
            probability_weight=50,
            is_active=True
        )

        good_mac = "AA:BB:CC:11:22:33"
        profile = DeviceProfile.objects.create(mac_address=good_mac, points=50)

        client = APIClient()
        with patch("portal.views._get_mac_address", return_value=good_mac):
            resp = client.post("/api/execute_spin/", {}, format="json")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertIn("prize", data)
            self.assertIn("name", data["prize"])
            self.assertIn("minutes", data["prize"])
            self.assertIn("mid_deg", data["prize"])
            self.assertIn("updated", data)
            self.assertEqual(data["updated"]["points"], 40)
            self.assertEqual(data["updated"]["remaining_spins"], 4)

            # Check profile updated in DB
            profile.refresh_from_db()
            self.assertEqual(profile.points, 40)
            self.assertEqual(profile.spins_today, 1)

        # Test spin with existing active session
        test_plan = Plan.objects.create(name="Spin Plan", price=10, duration_minutes=30)
        session = Session.objects.create(
            mac_address=good_mac,
            plan=test_plan,
            duration_minutes_purchased=30,
            amount_paid=10,
            status="active"
        )
        with patch("portal.views._get_mac_address", return_value=good_mac):
            resp2 = client.post("/api/execute_spin/", {}, format="json")
            self.assertEqual(resp2.status_code, 200)
            data2 = resp2.json()
            self.assertEqual(data2["status"], "success")
            session.refresh_from_db()
            if data2["prize"]["minutes"] > 0:
                self.assertGreater(session.duration_minutes_purchased, 30)

    def test_voucher_extension_rejects_blocked_device(self):
        client = APIClient()
        resp = client.post("/api/session/extend/", {
            "voucher_code": "VOUCH1",
            "mac_address": self.test_mac,
        })
        self.assertEqual(resp.status_code, 403)
        self.assertIn("blocked", resp.json().get("error", ""))


class SettingsSystemHardeningTests(TestCase):
    def setUp(self):
        from dashboard.models import SystemSettings
        User = get_user_model()
        self.admin_user = User.objects.create_superuser('settings_admin', 'settings@test.com', 'password123')
        self.client.login(username='settings_admin', password='password123')
        self.settings = SystemSettings.get_settings()

    def test_settings_view_requires_admin(self):
        self.client.logout()
        resp = self.client.get('/iconnect-ops/settings/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/iconnect-ops/login/', resp.url)

    def test_settings_view_get(self):
        resp = self.client.get('/iconnect-ops/settings/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Coin Slot Countdown Engine")
        self.assertContains(resp, "Points / ₱1 Spent")

    def test_coin_timer_max_cannot_be_less_than_initial_timer(self):
        post_data = {
            'insert_coin_countdown_seconds': '200',
            'coin_timer_max_seconds': '100',
            'coin_timer_min_remaining_seconds': '15',
            'coin_timer_extension_seconds': '8',
        }
        resp = self.client.post('/iconnect-ops/settings/', post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        messages_list = list(resp.context['messages'])
        self.assertTrue(any("cannot be less than the Initial Coin Timer" in str(m) for m in messages_list))
        self.settings.refresh_from_db()
        self.assertNotEqual(self.settings.insert_coin_countdown_seconds, 200)

    def test_coin_timer_min_cannot_exceed_max(self):
        post_data = {
            'insert_coin_countdown_seconds': '60',
            'coin_timer_max_seconds': '50',
            'coin_timer_min_remaining_seconds': '60',
            'coin_timer_extension_seconds': '8',
        }
        resp = self.client.post('/iconnect-ops/settings/', post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        messages_list = list(resp.context['messages'])
        self.assertTrue(any("cannot exceed Coin Timer Maximum ceiling" in str(m) for m in messages_list))

    def test_coin_timer_valid_save(self):
        post_data = {
            'insert_coin_countdown_seconds': '150',
            'coin_timer_max_seconds': '240',
            'coin_timer_min_remaining_seconds': '20',
            'coin_timer_extension_seconds': '10',
            'points_per_peso': '2',
        }
        resp = self.client.post('/iconnect-ops/settings/', post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.insert_coin_countdown_seconds, 150)
        self.assertEqual(self.settings.coin_timer_max_seconds, 240)
        self.assertEqual(self.settings.coin_timer_min_remaining_seconds, 20)
        self.assertEqual(self.settings.coin_timer_extension_seconds, 10)
        self.assertEqual(self.settings.points_per_peso, 2)

    def test_clear_telegram_credentials(self):
        self.settings.telegram_bot_token = "123456789:ABCdef-gh1234_xyz12345678"
        self.settings.telegram_admin_chat_id = "12345678"
        self.settings.save()

        post_data = {
            'telegram_bot_token': '',
            'telegram_admin_chat_id': '',
        }
        resp = self.client.post('/iconnect-ops/settings/', post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.telegram_bot_token, "")
        self.assertEqual(self.settings.telegram_admin_chat_id, "")

    def test_telegram_invalid_token_rejected(self):
        post_data = {
            'telegram_bot_token': 'malformed_token_string',
        }
        resp = self.client.post('/iconnect-ops/settings/', post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        messages_list = list(resp.context['messages'])
        self.assertTrue(any("Telegram Bot Token format appears invalid" in str(m) for m in messages_list))

    def test_backup_database_sqlite(self):
        resp = self.client.get('/iconnect-ops/settings/backup/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(resp['Content-Type'], ['application/x-sqlite3', 'application/json'])
        if resp['Content-Type'] == 'application/x-sqlite3':
            # SQLite file header check
            self.assertTrue(resp.content.startswith(b'SQLite format 3\x00'))


class UsersSessionsHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.admin = User.objects.create_superuser("sess_admin", "admin@sess.com", "pass1234")
        self.client.login(username="sess_admin", password="pass1234")
        from django.utils import timezone
        self.now = timezone.now()
        self.plan = Plan.objects.create(name="1 Hour Standard", price=10, duration_minutes=60, speed_limit=2.0)

    def test_resume_blacklisted_device_rejected(self):
        # Paused session for a device that was later blacklisted
        session = Session.objects.create(
            mac_address="DE:AD:BE:EF:00:01",
            ip_address="10.0.0.50",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="paused",
            time_in=self.now,
            paused_at=self.now
        )
        SuspiciousDevice.objects.create(
            mac_address="DE:AD:BE:EF:00:01",
            status=SuspiciousDevice.STATUS_BLOCKED,
            is_blocked=True
        )

        resp = self.client.post(f"/iconnect-ops/sessions/{session.id}/resume/")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["success"])
        self.assertIn("blocked", resp.json()["error"].lower())

        session.refresh_from_db()
        self.assertEqual(session.status, "paused")

    def test_resume_expired_paused_session_rejected(self):
        from datetime import timedelta
        # Session started 2 hours ago with 60 mins duration; remaining time is 0
        session = Session.objects.create(
            mac_address="AA:BB:CC:00:00:02",
            ip_address="10.0.0.51",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="paused",
            time_in=self.now - timedelta(minutes=120),
            paused_at=self.now - timedelta(minutes=10)
        )

        resp = self.client.post(f"/iconnect-ops/sessions/{session.id}/resume/")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])
        self.assertIn("expired", resp.json()["error"].lower())

        session.refresh_from_db()
        self.assertEqual(session.status, "expired")

    def test_add_time_blacklisted_device_rejected(self):
        session = Session.objects.create(
            mac_address="DE:AD:BE:EF:00:03",
            ip_address="10.0.0.52",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )
        SuspiciousDevice.objects.create(
            mac_address="DE:AD:BE:EF:00:03",
            status=SuspiciousDevice.STATUS_BLOCKED,
            is_blocked=True
        )

        resp = self.client.post(
            f"/iconnect-ops/sessions/{session.id}/add_time/",
            {"minutes": 30},
            format="json"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["success"])
        self.assertIn("blocked", resp.json()["error"].lower())

    def test_admin_resume_all_sessions_skips_blocked(self):
        # 1 valid paused session
        s1 = Session.objects.create(
            mac_address="AA:BB:CC:00:00:10",
            ip_address="10.0.0.60",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="paused",
            time_in=self.now,
            paused_at=self.now
        )
        # 1 blocked paused session
        s2 = Session.objects.create(
            mac_address="DE:AD:BE:EF:00:20",
            ip_address="10.0.0.61",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="paused",
            time_in=self.now,
            paused_at=self.now
        )
        SuspiciousDevice.objects.create(
            mac_address="DE:AD:BE:EF:00:20",
            status=SuspiciousDevice.STATUS_BLOCKED,
            is_blocked=True
        )

        resp = self.client.post("/iconnect-ops/sessions/resume-all/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["resumed_count"], 1)
        self.assertEqual(data["skipped_blocked_count"], 1)

        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.status, "active")
        self.assertEqual(s2.status, "paused")

    def test_export_sessions_csv_sanitizes_formulas(self):
        Session.objects.create(
            mac_address="AA:BB:CC:00:00:30",
            ip_address="+10.0.0.70",
            device_name="=cmd|' /C calc'!A0",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )

        resp = self.client.get("/iconnect-ops/sessions/export/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")

        # Must sanitize cells starting with '=' and '+'
        self.assertIn("'=cmd|' /C calc'!A0", content)
        self.assertIn("'+10.0.0.70", content)

    def test_sessions_live_api_pagination(self):
        # Create 30 active sessions
        for i in range(30):
            Session.objects.create(
                mac_address=f"AA:BB:CC:00:01:{i:02x}",
                ip_address=f"10.0.1.{i+1}",
                device_name=f"Device {i}",
                plan=self.plan,
                amount_paid=10,
                duration_minutes_purchased=60,
                status="active",
                time_in=self.now
            )

        resp = self.client.get("/api/dashboard/sessions/live/?page=2")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_pages"], 2)
        self.assertEqual(data["current_page"], 2)
        self.assertEqual(len(data["sessions"]), 5)


class DashboardOverviewHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.admin_user = User.objects.create_user(
            username="overview_admin",
            password="admin123password",
            is_staff=True,
            is_superuser=True,
        )
        self.now = timezone.now()
        self.plan = Plan.objects.create(
            name="1 Hour Regular",
            price=10.00,
            duration_minutes=60,
            is_active=True
        )

    def test_system_stats_api_requires_admin_auth(self):
        # Unauthenticated request must be blocked
        resp = self.client.get("/api/dashboard/system/")
        self.assertIn(resp.status_code, (401, 403))

        # Authenticated non-staff user must be blocked
        User = get_user_model()
        User.objects.create_user(username="regular_student", password="password123")
        self.client.login(username="regular_student", password="password123")
        student_resp = self.client.get("/api/dashboard/system/")
        self.assertIn(student_resp.status_code, (401, 403))

        # Authenticated admin request must succeed
        self.client.login(username=self.admin_user.username, password="admin123password")
        auth_resp = self.client.get("/api/dashboard/system/")
        self.assertEqual(auth_resp.status_code, 200)
        data = auth_resp.json()
        self.assertIn("cpu_load", data)
        self.assertIn("ram_percent", data)
        self.assertIn("disk_percent", data)
        self.assertIn("internet_online", data)

    @patch("sessions_app.internet_monitor.probe_upstream_internet")
    def test_system_stats_api_caches_internet_probe(self, mock_probe):
        from django.core.cache import cache
        cache.delete("dashboard_system_internet_online")
        mock_probe.return_value = True

        self.client.login(username=self.admin_user.username, password="admin123password")
        
        # First call triggers probe
        resp1 = self.client.get("/api/dashboard/system/")
        self.assertEqual(resp1.status_code, 200)
        self.assertTrue(resp1.json()["internet_online"])
        self.assertEqual(mock_probe.call_count, 1)

        # Second call uses cache within 15 seconds
        resp2 = self.client.get("/api/dashboard/system/")
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.json()["internet_online"])
        self.assertEqual(mock_probe.call_count, 1)

    @patch("sessions_app.tasks.cleanup_expired_and_stale_sessions")
    def test_dashboard_stats_api_throttles_cleanup(self, mock_cleanup):
        from django.core.cache import cache
        cache.delete("cleanup_sessions_throttle")

        self.client.login(username=self.admin_user.username, password="admin123password")

        # First request should call cleanup and set cache
        resp1 = self.client.get("/api/dashboard/stats/")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(mock_cleanup.call_count, 1)
        self.assertTrue(cache.get("cleanup_sessions_throttle"))

        # Subsequent immediate request within 20s should NOT trigger cleanup again
        resp2 = self.client.get("/api/dashboard/stats/")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(mock_cleanup.call_count, 1)

    def test_dashboard_stats_api_returns_weekly_and_monthly_revenue(self):
        # Create sessions for today, earlier this week, and earlier this month
        Session.objects.create(
            mac_address="AA:BB:CC:11:11:11",
            plan=self.plan,
            amount_paid=15.00,
            duration_minutes_purchased=90,
            status="active",
            time_in=self.now
        )

        self.client.login(username=self.admin_user.username, password="admin123password")
        resp = self.client.get("/api/dashboard/stats/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("revenue_this_week", data)
        self.assertIn("revenue_this_month", data)
        self.assertEqual(data["revenue_today"], 15.00)
        self.assertGreaterEqual(data["revenue_this_week"], 15.00)
        self.assertGreaterEqual(data["revenue_this_month"], 15.00)

    def test_overview_recent_sessions_ordering(self):
        # Create three sessions with staggered time_in
        s_old = Session.objects.create(
            mac_address="AA:BB:CC:11:00:01",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="expired",
            time_in=self.now - timedelta(hours=3)
        )
        s_mid = Session.objects.create(
            mac_address="AA:BB:CC:11:00:02",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="expired",
            time_in=self.now - timedelta(hours=1)
        )
        s_new = Session.objects.create(
            mac_address="AA:BB:CC:11:00:03",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )

        self.client.login(username=self.admin_user.username, password="admin123password")
        resp = self.client.get("/iconnect-ops/")
        self.assertEqual(resp.status_code, 200)

        sessions_in_context = list(resp.context["recent_sessions"])
        self.assertGreaterEqual(len(sessions_in_context), 3)
        # Most recent session must be first
        self.assertEqual(sessions_in_context[0].id, s_new.id)
        self.assertEqual(sessions_in_context[1].id, s_mid.id)
        self.assertEqual(sessions_in_context[2].id, s_old.id)


class RevenueHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.admin_user = User.objects.create_user(
            username="revenue_admin",
            password="admin123password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username=self.admin_user.username, password="admin123password")
        self.now = timezone.now()
        self.today = timezone.localdate()
        self.plan = Plan.objects.create(
            name="1 Hour Regular",
            price=10.00,
            duration_minutes=60,
            is_active=True
        )

    @patch("sessions_app.iptables.block_device")
    def test_reset_sales_disconnects_active_sessions_and_clears_summaries(self, mock_block_device):
        # Create active and expired sessions
        s_active = Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:01",
            ip_address="10.0.0.50",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )
        s_expired = Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:02",
            ip_address="10.0.0.51",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="expired",
            time_in=self.now - timedelta(hours=2)
        )
        CoinEvent.objects.create(amount=10, denomination=10, timestamp=self.now)
        DailyRevenueSummary.objects.create(
            date=self.today,
            total_revenue=20,
            total_sessions=2,
            avg_session_minutes=60,
            peak_hour=14
        )

        resp = self.client.post("/iconnect-ops/revenue/", {
            "action": "reset_sales",
            "start_date": self.today.strftime("%Y-%m-%d"),
            "end_date": self.today.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("reset=success", resp.url)

        # iptables rule must have been removed for the active session
        mock_block_device.assert_called_with("AA:BB:CC:DD:EE:01")

        # Database rows should be cleared
        self.assertEqual(Session.objects.count(), 0)
        self.assertEqual(CoinEvent.objects.count(), 0)
        self.assertEqual(DailyRevenueSummary.objects.filter(date=self.today).count(), 0)

    def test_update_goal_parsing_and_validation(self):
        # Valid update with commas and decimals
        resp = self.client.post("/iconnect-ops/revenue/", {
            "action": "update_goal",
            "daily_target": "1,250.00",
            "weekly_target": "7,500",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        
        daily_g = RevenueGoal.objects.get(period="daily")
        weekly_g = RevenueGoal.objects.get(period="weekly")
        self.assertEqual(daily_g.target_amount, 1250)
        self.assertEqual(weekly_g.target_amount, 7500)

        # Invalid target must be rejected with error
        bad_resp = self.client.post("/iconnect-ops/revenue/", {
            "action": "update_goal",
            "daily_target": "not_a_number",
            "weekly_target": "7,500",
        }, follow=True)
        self.assertContains(bad_resp, "Invalid daily target amount")

    def test_revenue_period_aliases(self):
        # Weekly alias test
        resp_week = self.client.get("/iconnect-ops/revenue/?period=weekly")
        self.assertEqual(resp_week.status_code, 200)
        self.assertEqual(resp_week.context["period"], "week")

        # Monthly alias test
        resp_month = self.client.get("/iconnect-ops/revenue/?period=monthly")
        self.assertEqual(resp_month.status_code, 200)
        self.assertEqual(resp_month.context["period"], "month")

        # Custom date fallback
        custom_date = (self.today - timedelta(days=2)).strftime("%Y-%m-%d")
        resp_custom = self.client.get(f"/iconnect-ops/revenue/?start_date={custom_date}&end_date={custom_date}")
        self.assertEqual(resp_custom.status_code, 200)
        self.assertEqual(resp_custom.context["period"], "custom")

    def test_revenue_live_api_targets_and_search(self):
        RevenueGoal.objects.create(period="daily", target_amount=100)
        RevenueGoal.objects.create(period="weekly", target_amount=700)
        CoinEvent.objects.create(amount=50, denomination=50, timestamp=self.now)

        Session.objects.create(
            mac_address="AA:BB:CC:99:88:77",
            ip_address="10.0.0.99",
            device_name="SpecialPhone",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )
        Session.objects.create(
            mac_address="11:22:33:44:55:66",
            ip_address="10.0.0.100",
            device_name="OtherLaptop",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )

        # Call live API
        resp = self.client.get("/api/dashboard/revenue/live/?period=weekly&search=SpecialPhone")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Goal progress verified
        self.assertEqual(data["daily_target_amt"], 100)
        self.assertEqual(data["today_sales_for_goal"], 50)
        self.assertEqual(data["daily_progress"], 50)

        # Search filter verified
        self.assertEqual(len(data["sessions"]), 1)
        self.assertEqual(data["sessions"][0]["device_name"], "SpecialPhone")

    def test_export_sessions_csv_period_sync(self):
        Session.objects.create(
            mac_address="AA:BB:CC:22:22:22",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )
        resp = self.client.get("/iconnect-ops/sessions/export/?period=weekly")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("AA:BB:CC:22:22:22", resp.content.decode("utf-8"))


class HeatmapHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.admin_user = User.objects.create_user(
            username="heatmap_admin",
            password="admin123password",
            is_staff=True,
            is_superuser=True,
        )
        self.regular_user = User.objects.create_user(
            username="regular_guest",
            password="guest123password",
            is_staff=False,
            is_superuser=False,
        )
        self.now = timezone.now()
        self.today = timezone.localdate()
        self.plan = Plan.objects.create(
            name="1 Hour Regular",
            price=10.00,
            duration_minutes=60,
            is_active=True
        )

    def test_heatmap_view_auth_and_periods(self):
        # Anonymous redirect
        anon_resp = self.client.get("/iconnect-ops/heatmap/")
        self.assertEqual(anon_resp.status_code, 302)

        # Login admin
        self.client.login(username=self.admin_user.username, password="admin123password")

        # Week (default)
        resp_week = self.client.get("/iconnect-ops/heatmap/?period=weekly")
        self.assertEqual(resp_week.status_code, 200)
        self.assertEqual(resp_week.context["period"], "week")
        self.assertIn("initial_heatmap_json", resp_week.context)

        # Month
        resp_month = self.client.get("/iconnect-ops/heatmap/?period=monthly")
        self.assertEqual(resp_month.status_code, 200)
        self.assertEqual(resp_month.context["period"], "month")

        # All Time
        resp_all = self.client.get("/iconnect-ops/heatmap/?period=all")
        self.assertEqual(resp_all.status_code, 200)
        self.assertEqual(resp_all.context["period"], "all")

    def test_heatmap_data_api_auth(self):
        # Anonymous 401
        anon_resp = self.client.get("/api/dashboard/heatmap/")
        self.assertIn(anon_resp.status_code, (401, 403))

        # Regular user 401/403
        self.client.login(username=self.regular_user.username, password="guest123password")
        reg_resp = self.client.get("/api/dashboard/heatmap/")
        self.assertIn(reg_resp.status_code, (401, 403))

    def test_heatmap_data_api_aggregation_revenue_and_caching(self):
        cache.clear()
        self.client.login(username=self.admin_user.username, password="admin123password")

        # Create 2 sessions today: morning and afternoon
        # Morning session at 9:00 AM local time
        morning_time = timezone.now().replace(hour=9, minute=0, second=0)
        afternoon_time = timezone.now().replace(hour=14, minute=0, second=0)

        Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:01",
            plan=self.plan,
            amount_paid=15,
            duration_minutes_purchased=60,
            status="active",
            time_in=morning_time
        )
        Session.objects.create(
            mac_address="AA:BB:CC:DD:EE:02",
            plan=self.plan,
            amount_paid=25,
            duration_minutes_purchased=60,
            status="active",
            time_in=afternoon_time
        )

        resp = self.client.get("/api/dashboard/heatmap/?period=week")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["period"], "week")
        self.assertEqual(data["total_sessions"], 2)
        self.assertEqual(data["total_revenue"], 40.0)
        self.assertGreaterEqual(len(data["heatmap"]), 1)

        # First item has both count and revenue
        item = data["heatmap"][0]
        self.assertIn("count", item)
        self.assertIn("revenue", item)

        # Caching verified
        cached = cache.get("dashboard_heatmap_data_week")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["total_sessions"], 2)


class AnalyticsHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.admin_user = User.objects.create_user(
            username="analytics_admin",
            password="admin123password",
            is_staff=True,
            is_superuser=True,
        )
        self.regular_user = User.objects.create_user(
            username="analytics_guest",
            password="guest123password",
            is_staff=False,
            is_superuser=False,
        )
        self.now = timezone.now()
        self.today = timezone.localdate()
        self.plan = Plan.objects.create(
            name="1 Hour Regular",
            price=10.00,
            duration_minutes=60,
            is_active=True
        )

    def test_analytics_auth_and_access(self):
        # Anonymous redirect
        resp = self.client.get("/iconnect-ops/analytics/")
        self.assertEqual(resp.status_code, 302)

        # Staff user gets 200
        self.client.login(username=self.admin_user.username, password="admin123password")
        resp = self.client.get("/iconnect-ops/analytics/")
        self.assertEqual(resp.status_code, 200)

    def test_analytics_retention_rate_with_kiosk_history(self):
        self.client.login(username=self.admin_user.username, password="admin123password")
        
        # MAC A: 1 past session (5 days ago), and 1 session today -> Returning device!
        past_time = self.now - timedelta(days=5)
        Session.objects.create(
            mac_address="AA:00:00:00:00:01",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="expired",
            time_in=past_time
        )
        Session.objects.create(
            mac_address="AA:00:00:00:00:01",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )

        # MAC B: only 1 session today -> First-time device
        Session.objects.create(
            mac_address="BB:00:00:00:00:02",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )

        # MAC C: 2 sessions today -> Returning device (multi-session)
        Session.objects.create(
            mac_address="CC:00:00:00:00:03",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="expired",
            time_in=self.now - timedelta(hours=2)
        )
        Session.objects.create(
            mac_address="CC:00:00:00:00:03",
            plan=self.plan,
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )

        resp = self.client.get("/iconnect-ops/analytics/?period=today")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["unique_devices"], 3)
        self.assertEqual(resp.context["returning_devices"], 2)
        self.assertEqual(resp.context["first_time_devices"], 1)
        self.assertEqual(resp.context["retention_rate"], 66.7)

    def test_analytics_custom_date_range_ordering_and_filtering(self):
        self.client.login(username=self.admin_user.username, password="admin123password")

        # Inverted start and end dates
        start_str = (self.today + timedelta(days=2)).isoformat()
        end_str = (self.today - timedelta(days=2)).isoformat()
        resp = self.client.get(f"/iconnect-ops/analytics/?period=custom&custom_start={start_str}&custom_end={end_str}")
        self.assertEqual(resp.status_code, 200)
        # Should have swapped start_date and end_date without errors
        self.assertLessEqual(resp.context["start_date"], resp.context["end_date"])

    def test_revenue_data_api_custom_range_and_paused_sessions(self):
        self.client.login(username=self.admin_user.username, password="admin123password")

        day1 = self.today - timedelta(days=5)
        day2 = self.today - timedelta(days=2)
        out_day = self.today + timedelta(days=2)

        # Session within custom range with status='paused'
        Session.objects.create(
            mac_address="11:22:33:44:55:66",
            plan=self.plan,
            amount_paid=20,
            duration_minutes_purchased=60,
            status="paused",
            time_in=self.now.replace(year=day1.year, month=day1.month, day=day1.day, hour=10, minute=0, second=0)
        )

        # Session outside custom range
        Session.objects.create(
            mac_address="11:22:33:44:55:77",
            plan=self.plan,
            amount_paid=50,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now.replace(year=out_day.year, month=out_day.month, day=out_day.day, hour=10, minute=0, second=0)
        )

        # Call revenue API with custom dates
        resp = self.client.get(f"/api/dashboard/revenue/?period=custom&custom_start={day1.isoformat()}&custom_end={day2.isoformat()}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("revenue_data", data)
        self.assertEqual(data["period_revenue_total"], 20.0)

        # Test inverted dates in API
        resp_inv = self.client.get(f"/api/dashboard/revenue/?period=custom&custom_start={day2.isoformat()}&custom_end={day1.isoformat()}")
        self.assertEqual(resp_inv.status_code, 200)
        data_inv = resp_inv.json()
        self.assertEqual(data_inv["period_revenue_total"], 20.0)

    def test_analytics_group_pass_included_in_plan_stats(self):
        self.client.login(username=self.admin_user.username, password="admin123password")

        vip_plan = Plan.objects.create(
            name="VIP Group Pass Plan",
            price=50,
            duration_minutes=120,
            is_active=True
        )
        group = SessionGroup.objects.create(
            group_code="GRP999",
            plan=vip_plan,
            max_devices=5,
            total_price=50,
            duration_minutes=120
        )
        # Group session with amount_paid=0 but session_group assigned
        Session.objects.create(
            mac_address="99:88:77:66:55:44",
            plan=vip_plan,
            session_group=group,
            amount_paid=0,
            duration_minutes_purchased=120,
            status="active",
            time_in=self.now
        )
        # Prize session with amount_paid=0 and name starting with "Prize:"
        prize_plan = Plan.objects.create(
            name="Prize: 15 Mins",
            price=0,
            duration_minutes=15,
            is_active=True
        )
        Session.objects.create(
            mac_address="88:77:66:55:44:33",
            plan=prize_plan,
            amount_paid=0,
            duration_minutes_purchased=15,
            status="active",
            time_in=self.now
        )

        resp = self.client.get("/iconnect-ops/analytics/?period=today")
        self.assertEqual(resp.status_code, 200)
        plan_stats = resp.context["plan_stats"]
        plan_names = [p["plan__name"] for p in plan_stats]
        self.assertIn("VIP Group Pass Plan", plan_names)
        self.assertNotIn("Prize: 15 Mins", plan_names)

    def test_analytics_dynamic_revenue_growth_periods(self):
        self.client.login(username=self.admin_user.username, password="admin123password")

        for p in ["today", "week", "month", "year", "all"]:
            resp = self.client.get(f"/iconnect-ops/analytics/?period={p}")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("growth_label", resp.context)
            self.assertIn("growth_title", resp.context)
            self.assertIn("revenue_growth", resp.context)

    def test_analytics_template_rendering_escaped_plan_names(self):
        self.client.login(username=self.admin_user.username, password="admin123password")

        quote_plan = Plan.objects.create(
            name="Student's Special Pass \"Pro\"",
            price=25,
            duration_minutes=60,
            is_active=True
        )
        Session.objects.create(
            mac_address="22:33:44:55:66:77",
            plan=quote_plan,
            amount_paid=25,
            duration_minutes_purchased=60,
            status="active",
            time_in=self.now
        )

        resp = self.client.get("/iconnect-ops/analytics/?period=today")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Student&#x27;s Special Pass", content)
        self.assertIn("planLabels", content)


class SystemLogsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="logs_admin",
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username="logs_admin", password="admin123")

    def test_logs_view_renders_and_filters_mac(self):
        from sessions_app.models import CoinEvent, Session
        from django.utils import timezone

        sess = Session.objects.create(
            mac_address="AA:BB:CC:11:22:33",
            ip_address="10.0.0.5",
            amount_paid=10,
            duration_minutes_purchased=60,
            status="active",
            time_in=timezone.now(),
        )
        CoinEvent.objects.create(
            mac_address="AA:BB:CC:11:22:33",
            amount=10,
            denomination=10,
            session=sess,
            timestamp=timezone.now(),
        )
        CoinEvent.objects.create(
            mac_address="DD:EE:FF:44:55:66",
            amount=5,
            denomination=5,
            session=None,
            timestamp=timezone.now(),
        )

        resp = self.client.get("/iconnect-ops/logs/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "AA:BB:CC:11:22:33")
        self.assertContains(resp, "DD:EE:FF:44:55:66")
        self.assertContains(resp, f"Session #{sess.id}")

        # Test MAC search filter
        search_resp = self.client.get("/iconnect-ops/logs/?mac=AA:BB:CC")
        self.assertEqual(search_resp.status_code, 200)
        self.assertContains(search_resp, "AA:BB:CC:11:22:33")
        self.assertNotContains(search_resp, "DD:EE:FF:44:55:66")

















