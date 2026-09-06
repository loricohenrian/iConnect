from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import CoinEvent, CoinInsertRequest, Plan, Session, WhitelistedDevice, SuspiciousDevice


class PlanModelTests(TestCase):
    def test_price_per_minute_rounds_to_two_decimals(self):
        plan = Plan.objects.create(name="P5 Plan", price=5, duration_minutes=30, is_active=True)
        self.assertEqual(plan.price_per_minute, 0.17)

    def test_price_per_minute_second_example(self):
        plan = Plan.objects.create(name="P10 Plan", price=10, duration_minutes=60, is_active=True)
        self.assertEqual(plan.price_per_minute, 0.17)


class SessionApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.plan = Plan.objects.create(
            name="P5 Plan",
            price=5,
            duration_minutes=30,
            is_active=True,
        )
        self.mac_one = "AA:BB:CC:DD:EE:01"
        self.mac_two = "AA:BB:CC:DD:EE:02"
        User = get_user_model()
        self.admin_password = "admin123"
        self.admin_user = User.objects.create_user(
            username="admin_test",
            password=self.admin_password,
            is_staff=True,
            is_superuser=True,
        )

    def _login_admin(self):
        logged_in = self.client.login(username=self.admin_user.username, password=self.admin_password)
        self.assertTrue(logged_in)

    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=True)
    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_session_start_uses_only_matching_mac_payment(self, allow_device_mock, baseline_mock):
        matching_event = CoinEvent.objects.create(
            amount=5,
            denomination=5,
            mac_address=self.mac_one,
        )
        other_event = CoinEvent.objects.create(
            amount=20,
            denomination=20,
            mac_address=self.mac_two,
        )

        response = self.client.post(
            reverse("sessions_app:session-start"),
            {
                "mac_address": self.mac_one,
                "plan_id": self.plan.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        session = Session.objects.get(mac_address=self.mac_one, status="active")
        matching_event.refresh_from_db()
        other_event.refresh_from_db()

        self.assertEqual(matching_event.session_id, session.id)
        self.assertIsNone(other_event.session_id)
        allow_device_mock.assert_called_once_with(self.mac_one, rate_kbps=None, upload_kbps=None)

    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=True)
    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_session_start_rejects_unscoped_or_other_device_payment(self, allow_device_mock, baseline_mock):
        CoinEvent.objects.create(
            amount=5,
            denomination=5,
            mac_address=self.mac_two,
        )
        CoinEvent.objects.create(
            amount=5,
            denomination=5,
            mac_address=None,
        )

        response = self.client.post(
            reverse("sessions_app:session-start"),
            {
                "mac_address": self.mac_one,
                "plan_id": self.plan.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(Session.objects.count(), 0)
        allow_device_mock.assert_not_called()

    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=False)
    def test_session_start_blocks_when_firewall_baseline_not_ready(self, baseline_mock, allow_device_mock):
        response = self.client.post(
            reverse("sessions_app:session-start"),
            {
                "mac_address": self.mac_one,
                "plan_id": self.plan.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Firewall baseline is not ready", response.json()["error"])
        baseline_mock.assert_called_once()
        allow_device_mock.assert_not_called()

    def test_session_extend_rejects_voucher_for_different_device(self):
        Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=30,
            remaining_minutes=30,
            amount_paid=5,
            status="paused",
            voucher_code="ABC123",
        )

        response = self.client.post(
            reverse("sessions_app:session-extend"),
            {
                "voucher_code": "ABC123",
                "mac_address": self.mac_two,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=False)
    def test_session_extend_new_session_blocks_when_firewall_baseline_not_ready(self, baseline_mock, allow_device_mock):
        Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=30,
            remaining_minutes=30,
            amount_paid=5,
            status="paused",
            voucher_code="EXT123",
        )

        response = self.client.post(
            reverse("sessions_app:session-extend"),
            {
                "voucher_code": "EXT123",
                "mac_address": self.mac_one,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Firewall baseline is not ready", response.json()["error"])
        baseline_mock.assert_called_once()
        allow_device_mock.assert_not_called()

    @override_settings(PISONET_VOUCHER_MAX_ATTEMPTS=1, PISONET_VOUCHER_WINDOW_SECONDS=300)
    def test_session_extend_rate_limit_triggers(self):
        cache.clear()

        first = self.client.post(
            reverse("sessions_app:session-extend"),
            {
                "voucher_code": "NOPE01",
                "mac_address": self.mac_one,
            },
            format="json",
        )
        self.assertEqual(first.status_code, 404)

        second = self.client.post(
            reverse("sessions_app:session-extend"),
            {
                "voucher_code": "NOPE02",
                "mac_address": self.mac_one,
            },
            format="json",
        )
        self.assertEqual(second.status_code, 429)

    @patch("sessions_app.views.iptables.whitelist_device", return_value=True)
    def test_whitelist_device_applies_firewall_rule(self, whitelist_mock):
        self._login_admin()
        response = self.client.post(
            reverse("sessions_app:whitelist"),
            {
                "mac_address": self.mac_one,
                "device_name": "Admin Laptop",
                "added_by": "admin",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(WhitelistedDevice.objects.filter(mac_address=self.mac_one).exists())
        whitelist_mock.assert_called_once_with(self.mac_one)

    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=True)
    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_group_pass_generates_5_digit_code(self, allow_mock, base_mock):
        CoinEvent.objects.create(
            amount=10,
            denomination=10,
            mac_address=self.mac_one,
        )
        response = self.client.post(
            reverse("sessions_app:session-start"),
            {
                "mac_address": self.mac_one,
                "plan_id": self.plan.id,
                "is_group_pass": True,
                "group_pass_devices": 2,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        session = Session.objects.get(mac_address=self.mac_one, status="active")
        self.assertIsNotNone(session.session_group)
        self.assertEqual(len(session.session_group.group_code), 5)
        self.assertTrue(session.session_group.group_code.isalnum())

    def test_protected_endpoints_require_admin_auth(self):
        checks = [
            ("post", reverse("sessions_app:whitelist"), {"mac_address": self.mac_one, "device_name": "X"}),
            ("post", reverse("sessions_app:session-end"), {"mac_address": self.mac_one}),
            ("get", reverse("sessions_app:connected-users"), None),
            ("get", reverse("sessions_app:bandwidth"), None),
        ]

        for method, url, payload in checks:
            if method == "post":
                response = self.client.post(url, payload, format="json")
            else:
                response = self.client.get(url)
            self.assertIn(response.status_code, (401, 403))

    @override_settings(PISONET_DEVICE_API_KEY="test-device-key")
    def test_coin_inserted_requires_device_api_key(self):
        no_key = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 5, "denomination": 5, "mac_address": self.mac_one},
            format="json",
        )
        self.assertEqual(no_key.status_code, 401)

        with_key = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 5, "denomination": 5, "mac_address": self.mac_one},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )
        self.assertEqual(with_key.status_code, 201)

    @override_settings(
        PISONET_DEVICE_API_KEY="test-device-key",
        PISONET_COIN_MAX_REQUESTS=1,
        PISONET_COIN_WINDOW_SECONDS=60,
    )
    def test_coin_inserted_rate_limit_triggers(self):
        from django.core.cache import cache
        cache.clear()
        first = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 5, "denomination": 5, "mac_address": self.mac_one},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )
        self.assertEqual(first.status_code, 201)

        second = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 5, "denomination": 5, "mac_address": self.mac_one},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )
        self.assertEqual(second.status_code, 429)

    @override_settings(
        PISONET_DEVICE_API_KEY="test-device-key",
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "coin-validation-test",
            }
        },
    )
    def test_coin_inserted_rejects_amount_denomination_mismatch(self):
        response = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 10, "denomination": 5, "mac_address": self.mac_one},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("amount", response.json())

    @override_settings(
        PISONET_DEVICE_API_KEY="test-device-key",
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "coin-unscoped-test",
            }
        },
    )
    def test_unscoped_coin_insert_logged_as_unassigned_revenue(self):
        # No request created, coin sent unscoped (no mac_address)
        response = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 5, "denomination": 5},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(CoinEvent.objects.count(), 1)
        event = CoinEvent.objects.first()
        self.assertIsNone(event.mac_address)
        self.assertIsNone(event.session)
        self.assertEqual(event.amount, 5)
        # Verify no active session was granted
        self.assertEqual(Session.objects.count(), 0)

    @override_settings(PISONET_DEVICE_API_KEY="test-device-key")
    def test_coinslot_status_lifecycle(self):
        # Initially no request -> disabled
        res_initial = self.client.get(reverse("sessions_app:coinslot-status"))
        self.assertEqual(res_initial.status_code, 200)
        self.assertFalse(res_initial.json()["enabled"])

        # User requests coin slot -> enabled
        req_res = self.client.post(
            reverse("sessions_app:session-start-request"),
            {"mac_address": self.mac_one, "plan_id": self.plan.id},
            format="json",
        )
        self.assertEqual(req_res.status_code, 201)

        res_active = self.client.get(reverse("sessions_app:coinslot-status"))
        self.assertEqual(res_active.status_code, 200)
        self.assertTrue(res_active.json()["enabled"])
        self.assertEqual(res_active.json()["mac_address"], self.mac_one)

    def test_public_endpoints_stay_public_under_global_drf_defaults(self):
        responses = {
            "plans": self.client.get(reverse("sessions_app:plans-list")).status_code,
            "status": self.client.get(reverse("sessions_app:session-status"), {"mac_address": self.mac_one}).status_code,
            "speed": self.client.get(reverse("sessions_app:speed-test"), {"mac_address": self.mac_one}).status_code,
            "signal": self.client.get(reverse("sessions_app:signal-strength")).status_code,
            "start": self.client.post(
                reverse("sessions_app:session-start"),
                {"mac_address": self.mac_one, "plan_id": self.plan.id},
                format="json",
            ).status_code,
        }

        self.assertEqual(responses["plans"], 200)
        self.assertEqual(responses["signal"], 200)
        self.assertIn(responses["status"], (200, 404))
        self.assertEqual(responses["speed"], 404)
        self.assertIn(responses["start"], (402, 503))

    @override_settings(PISONET_PUBLIC_MAX_REQUESTS=1, PISONET_PUBLIC_WINDOW_SECONDS=300)
    def test_public_plans_endpoint_rate_limit_triggers(self):
        cache.clear()

        first = self.client.get(reverse("sessions_app:plans-list"))
        self.assertEqual(first.status_code, 200)

        second = self.client.get(reverse("sessions_app:plans-list"))
        self.assertEqual(second.status_code, 429)

    def test_speed_test_requires_mac_address(self):
        response = self.client.get(reverse("sessions_app:speed-test"))
        self.assertEqual(response.status_code, 400)

    def test_speed_test_returns_metrics_for_active_session(self):
        Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            remaining_minutes=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            ip_address="127.0.0.1",
        )

        response = self.client.get(
            reverse("sessions_app:speed-test"),
            {"mac_address": self.mac_one},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("download_mbps", body)
        self.assertIn("upload_mbps", body)
        self.assertIn("ping_ms", body)
        self.assertIn("speed_mode", body)
        self.assertIn("mode_label", body)
        self.assertEqual(body["speed_mode"], "estimated")

    def test_session_status_rejects_ip_mismatch(self):
        Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            remaining_minutes=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            ip_address="10.0.0.99",
        )

        response = self.client.get(
            reverse("sessions_app:session-status"),
            {"mac_address": self.mac_one},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "active")
        
        # Check IP was synced
        session = Session.objects.get(mac_address=self.mac_one, status="active")
        self.assertEqual(session.ip_address, "127.0.0.1")

    def test_speed_test_rejects_ip_mismatch(self):
        Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            remaining_minutes=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            ip_address="10.0.0.99",
        )

        response = self.client.get(
            reverse("sessions_app:speed-test"),
            {"mac_address": self.mac_one},
        )

        self.assertEqual(response.status_code, 200)
        
        # Check IP was synced
        session = Session.objects.get(mac_address=self.mac_one, status="active")
        self.assertEqual(session.ip_address, "127.0.0.1")

    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_session_start_detects_suspected_clone(self, allow_device_mock):
        Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            ip_address="10.0.0.99",
        )

        response = self.client.post(
            reverse("sessions_app:session-start"),
            {
                "mac_address": self.mac_one,
                "plan_id": self.plan.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(SuspiciousDevice.objects.filter(mac_address=self.mac_one).exists())
        
        # Check IP was synced
        session = Session.objects.get(mac_address=self.mac_one, status="active")
        self.assertEqual(session.ip_address, "127.0.0.1")

    @patch("sessions_app.bandwidth.get_device_bandwidth_mb", return_value=5.0)
    def test_session_status_updates_bandwidth_usage(self, mock_bandwidth):
        session = Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            time_in=timezone.now() - timezone.timedelta(minutes=10),
            duration_minutes_purchased=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            ip_address="127.0.0.1",
            bandwidth_used_mb=0,
        )

        response = self.client.get(
            reverse("sessions_app:session-status"),
            {"mac_address": self.mac_one},
        )

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertGreater(session.bandwidth_used_mb, 0)

    @override_settings(PISONET_DEVICE_API_KEY="test-device-key")
    def test_session_start_request_creates_queue_entry(self):
        response = self.client.post(
            reverse("sessions_app:session-start-request"),
            {
                "mac_address": self.mac_one,
                "plan_id": self.plan.id,
            },
            format="json",
        )

        self.assertIn(response.status_code, (200, 201))
        body = response.json()
        self.assertEqual(body["status"], "success")
        self.assertIn("coin_request", body)

        req = CoinInsertRequest.objects.get(id=body["coin_request"]["id"])
        self.assertEqual(req.mac_address, self.mac_one)
        self.assertEqual(req.expected_amount, self.plan.price)

    @override_settings(PISONET_DEVICE_API_KEY="test-device-key")
    def test_unscoped_coin_insert_assigns_to_active_queue_request(self):
        request_response = self.client.post(
            reverse("sessions_app:session-start-request"),
            {
                "mac_address": self.mac_one,
                "plan_id": self.plan.id,
            },
            format="json",
        )
        self.assertIn(request_response.status_code, (200, 201))

        coin_response = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 5, "denomination": 5},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )

        self.assertEqual(coin_response.status_code, 201)
        coin_body = coin_response.json()
        self.assertEqual(coin_body["assigned_mac_address"], self.mac_one)
        self.assertIsNotNone(coin_body["coin_request"])

        coin_event = CoinEvent.objects.get(id=coin_body["coin_event_id"])
        self.assertEqual(coin_event.mac_address, self.mac_one)

    @override_settings(PISONET_DEVICE_API_KEY="test-device-key")
    def test_coin_inserted_extends_countdown_timer(self):
        from datetime import timedelta
        from dashboard.models import SystemSettings
        sys_settings = SystemSettings.get_settings()
        sys_settings.coin_timer_extension_seconds = 8
        sys_settings.coin_timer_min_remaining_seconds = 15
        sys_settings.coin_timer_max_seconds = 180
        sys_settings.save()

        now = timezone.now()
        req = CoinInsertRequest.objects.create(
            mac_address=self.mac_one,
            purpose=CoinInsertRequest.PURPOSE_START,
            plan=self.plan,
            expected_amount=self.plan.price,
            status=CoinInsertRequest.STATUS_ACTIVE,
            activated_at=now,
            expires_at=now + timedelta(seconds=30),
        )

        response = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 1, "denomination": 1},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )

        self.assertEqual(response.status_code, 201)
        req.refresh_from_db()
        remaining = (req.expires_at - timezone.now()).total_seconds()
        self.assertGreaterEqual(remaining, 35)
        self.assertLessEqual(remaining, 39)

        body = response.json()
        self.assertIn("coin_request", body)
        self.assertGreaterEqual(body["coin_request"]["remaining_seconds"], 35)

    @override_settings(PISONET_DEVICE_API_KEY="test-device-key")
    def test_coin_inserted_enforces_minimum_guarantee_and_ceiling(self):
        from datetime import timedelta
        from dashboard.models import SystemSettings
        sys_settings = SystemSettings.get_settings()
        sys_settings.coin_timer_extension_seconds = 8
        sys_settings.coin_timer_min_remaining_seconds = 15
        sys_settings.coin_timer_max_seconds = 180
        sys_settings.save()

        # Case 1: Low remaining time (3s). Min guarantee of 15s should kick in.
        now = timezone.now()
        req = CoinInsertRequest.objects.create(
            mac_address=self.mac_one,
            purpose=CoinInsertRequest.PURPOSE_START,
            plan=self.plan,
            expected_amount=self.plan.price,
            status=CoinInsertRequest.STATUS_ACTIVE,
            activated_at=now,
            expires_at=now + timedelta(seconds=3),
        )

        response = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 1, "denomination": 1},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )
        self.assertEqual(response.status_code, 201)
        req.refresh_from_db()
        remaining = (req.expires_at - timezone.now()).total_seconds()
        self.assertGreaterEqual(remaining, 14)
        self.assertLessEqual(remaining, 16)

        # Case 2: Near ceiling (178s). Adding 8s is capped at 180s.
        req.expires_at = timezone.now() + timedelta(seconds=178)
        req.save()

        response2 = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 1, "denomination": 1},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )
        self.assertEqual(response2.status_code, 201)
        req.refresh_from_db()
        remaining2 = (req.expires_at - timezone.now()).total_seconds()
        self.assertLessEqual(remaining2, 180.5)
        self.assertGreaterEqual(remaining2, 178)

    def test_session_pause_rejects_ip_mismatch(self):
        Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            remaining_minutes=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            ip_address="10.0.0.99",
        )

        response = self.client.post(
            reverse("sessions_app:session-pause"),
            {"mac_address": self.mac_one},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        
        # Check IP was synced
        session = Session.objects.get(mac_address=self.mac_one)
        self.assertEqual(session.ip_address, "127.0.0.1")
        self.assertEqual(session.status, "paused")

    @override_settings(PISONET_MAX_PAUSE_HOURS=24)
    def test_session_resume_expires_when_max_pause_hours_exceeded(self):
        session = Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=30,
            amount_paid=5,
            status="paused",
            paused_at=timezone.now() - timezone.timedelta(hours=25),
            ip_address="127.0.0.1",
        )

        response = self.client.post(
            reverse("sessions_app:session-pause"),
            {"mac_address": self.mac_one},
            format="json",
        )

        self.assertEqual(response.status_code, 410)
        session.refresh_from_db()
        self.assertEqual(session.status, "expired")

    @patch("sessions_app.tasks._is_device_reachable", return_value=True)
    def test_manual_pause_does_not_auto_resume(self, mock_reachable):
        from dashboard.models import SystemSettings
        from .tasks import auto_resume_connected_sessions

        sys_settings = SystemSettings.get_settings()
        sys_settings.enable_auto_pause_resume = True
        sys_settings.save()

        session = Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            remaining_minutes=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            ip_address="127.0.0.1",
        )

        # Student manually pauses session
        response = self.client.post(
            reverse("sessions_app:session-pause"),
            {"mac_address": self.mac_one},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "paused")
        self.assertIn("pauses_left", response.data)

        session.refresh_from_db()
        self.assertEqual(session.status, "paused")

        # Celery background task runs while device is reachable
        result = auto_resume_connected_sessions()
        self.assertIn("auto-resumed 0", result)

        # Verify session is STILL paused (never auto-resumed)
        session.refresh_from_db()
        self.assertEqual(session.status, "paused")

        # Student manually clicks Resume
        resume_response = self.client.post(
            reverse("sessions_app:session-pause"),
            {"mac_address": self.mac_one},
            format="json",
        )
        self.assertEqual(resume_response.status_code, 200)
        self.assertEqual(resume_response.data["status"], "active")
        session.refresh_from_db()
        self.assertEqual(session.status, "active")

    @override_settings(PISONET_GPIO_SIMULATION=False)
    @patch("sessions_app.tasks._is_device_reachable")
    def test_auto_pause_can_auto_resume(self, mock_reachable):
        from dashboard.models import SystemSettings
        from .tasks import auto_resume_connected_sessions, auto_pause_disconnected_sessions

        sys_settings = SystemSettings.get_settings()
        sys_settings.enable_auto_pause_resume = True
        sys_settings.save()

        session = Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            remaining_minutes=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            ip_address="127.0.0.1",
        )

        # Device disconnects — simulate unreachable for > 300s
        mock_reachable.return_value = False
        cache_key = f"auto_pause_unreachable_{self.mac_one}"
        past_time = timezone.now() - timezone.timedelta(seconds=400)
        cache.set(cache_key, past_time.isoformat(), timeout=600)

        # Run auto-pause task
        auto_pause_disconnected_sessions()
        session.refresh_from_db()
        self.assertEqual(session.status, "paused")
        self.assertTrue(cache.get(f"auto_paused_{session.id}"))

        # Device reconnects — reachable again
        mock_reachable.return_value = True
        auto_resume_connected_sessions()

        # Session should now be auto-resumed because it was auto-paused
        session.refresh_from_db()
        self.assertEqual(session.status, "active")
        self.assertIsNone(cache.get(f"auto_paused_{session.id}"))

    def test_session_start_rejects_blocked_device(self):
        SuspiciousDevice.objects.create(
            mac_address=self.mac_one,
            reason="cheating",
            status=SuspiciousDevice.STATUS_BLOCKED,
            is_blocked=True,
        )
        CoinEvent.objects.create(amount=5, denomination=5, mac_address=self.mac_one)

        response = self.client.post(
            reverse("sessions_app:session-start"),
            {"mac_address": self.mac_one, "plan_id": self.plan.id},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("blocked", response.json()["error"].lower())

    def test_session_start_request_rejects_blocked_device(self):
        SuspiciousDevice.objects.create(
            mac_address=self.mac_one,
            reason="cheating",
            status=SuspiciousDevice.STATUS_BLOCKED,
            is_blocked=True,
        )

        response = self.client.post(
            reverse("sessions_app:session-start-request"),
            {"mac_address": self.mac_one, "plan_id": self.plan.id},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("blocked", response.json()["error"].lower())

    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_session_extend_paid_success(self, allow_device_mock):
        session = Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=30,
            amount_paid=5,
            status="active",
        )
        CoinEvent.objects.create(amount=5, denomination=5, mac_address=self.mac_one)

        response = self.client.post(
            reverse("sessions_app:session-extend-paid"),
            {"mac_address": self.mac_one, "plan_id": self.plan.id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.duration_minutes_purchased, 60)
        self.assertEqual(session.amount_paid, 10)

    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_session_extend_paid_twice(self, allow_device_mock):
        session = Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=30,
            amount_paid=5,
            status="active",
        )
        # First extension
        CoinEvent.objects.create(amount=5, denomination=5, mac_address=self.mac_one)
        res1 = self.client.post(
            reverse("sessions_app:session-extend-paid"),
            {"mac_address": self.mac_one, "plan_id": self.plan.id},
            format="json",
        )
        self.assertEqual(res1.status_code, 200)

        # Second extension
        CoinEvent.objects.create(amount=5, denomination=5, mac_address=self.mac_one)
        res2 = self.client.post(
            reverse("sessions_app:session-extend-paid"),
            {"mac_address": self.mac_one, "plan_id": self.plan.id},
            format="json",
        )
        self.assertEqual(res2.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.duration_minutes_purchased, 90)
        self.assertEqual(session.amount_paid, 15)

    @override_settings(PISONET_DEVICE_API_KEY="test-device-key")
    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=True)
    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_cancel_and_reinsert_coins_preserves_balance_and_ready_to_connect(self, allow_device_mock, baseline_mock):
        # 1. User requests coin slot
        res1 = self.client.post(
            reverse("sessions_app:session-start-request"),
            {"mac_address": self.mac_one, "plan_id": self.plan.id},
            format="json",
        )
        self.assertIn(res1.status_code, (200, 201))
        body1 = res1.json()
        req_id1 = body1["coin_request"]["id"]

        # 2. User inserts coins (₱5)
        self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 5, "denomination": 5},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )

        # 3. User cancels the request without starting session
        cancel_res = self.client.post(
            reverse("sessions_app:session-start-cancel"),
            {"mac_address": self.mac_one},
            format="json",
        )
        self.assertEqual(cancel_res.status_code, 200)
        req1 = CoinInsertRequest.objects.get(id=req_id1)
        self.assertEqual(req1.status, CoinInsertRequest.STATUS_CANCELLED)

        # 4. User clicks "Insert Coins" again
        res2 = self.client.post(
            reverse("sessions_app:session-start-request"),
            {"mac_address": self.mac_one, "plan_id": self.plan.id},
            format="json",
        )
        self.assertIn(res2.status_code, (200, 201))
        body2 = res2.json()
        self.assertEqual(body2["status"], "success")
        self.assertIn("coin_request", body2)
        coin_req2 = body2["coin_request"]

        # 5. Balance is preserved and user is immediately ready to connect
        self.assertEqual(coin_req2["credited_amount"], 5)
        self.assertTrue(coin_req2["ready_to_start"])

        # 6. User connects now
        start_res = self.client.post(
            reverse("sessions_app:session-start"),
            {"mac_address": self.mac_one, "plan_id": self.plan.id},
            format="json",
        )
        self.assertEqual(start_res.status_code, 201)
        active_sess = Session.objects.filter(mac_address=self.mac_one, status="active").first()
        self.assertIsNotNone(active_sess)

        # 7. Next request after session started should have 0 balance and ready_to_start False
        res3 = self.client.post(
            reverse("sessions_app:session-start-request"),
            {"mac_address": self.mac_one, "plan_id": self.plan.id},
            format="json",
        )
        self.assertIn(res3.status_code, (200, 201))
        body3 = res3.json()
        self.assertEqual(body3["coin_request"]["credited_amount"], 0)
        self.assertFalse(body3["coin_request"]["ready_to_start"])


class ComboPlanTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.mac = "AA:BB:CC:DD:EE:01"
        self.p1 = Plan.objects.create(name="₱1 Plan", price=1, duration_minutes=10, is_active=True, speed_limit=2)
        self.p5 = Plan.objects.create(name="₱5 Plan", price=5, duration_minutes=60, is_active=True, speed_limit=3)
        self.p10 = Plan.objects.create(name="₱10 Plan", price=10, duration_minutes=150, is_active=True, speed_limit=5)
        self.p20 = Plan.objects.create(name="₱20 Plan", price=20, duration_minutes=360, is_active=True, speed_limit=10)

    def test_calculate_combo_for_amount_7_pesos(self):
        from .views import calculate_combo_for_amount
        combo = calculate_combo_for_amount(7)
        self.assertIsNotNone(combo)
        self.assertEqual(combo["amount_used"], 7)
        # ₱7 = 1x ₱5 (60m) + 2x ₱1 (20m) = 80m
        self.assertEqual(combo["total_minutes"], 80)
        self.assertEqual(combo["duration_display"], "1h 20m")
        self.assertEqual(combo["highest_plan"], self.p5)

    def test_calculate_combo_for_amount_15_pesos(self):
        from .views import calculate_combo_for_amount
        combo = calculate_combo_for_amount(15)
        self.assertIsNotNone(combo)
        self.assertEqual(combo["amount_used"], 15)
        # ₱15 = 1x ₱10 (150m) + 1x ₱5 (60m) = 210m
        self.assertEqual(combo["total_minutes"], 210)
        self.assertEqual(combo["duration_display"], "3h 30m")
        self.assertEqual(combo["highest_plan"], self.p10)

    def test_session_start_request_without_plan_id(self):
        response = self.client.post(
            reverse("sessions_app:session-start-request"),
            {"mac_address": self.mac},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("coin_request", data)
        self.assertIsNone(data["coin_request"]["plan_id"])
        # Expected amount defaults to lowest plan price (₱1)
        self.assertEqual(data["coin_request"]["expected_amount"], 1)

    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=True)
    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_session_start_without_plan_id_and_combo_coins(self, allow_device_mock, baseline_mock):
        # Insert ₱7 (one ₱5 coin and two ₱1 coins)
        c1 = CoinEvent.objects.create(amount=5, denomination=5, mac_address=self.mac)
        c2 = CoinEvent.objects.create(amount=1, denomination=1, mac_address=self.mac)
        c3 = CoinEvent.objects.create(amount=1, denomination=1, mac_address=self.mac)

        response = self.client.post(
            reverse("sessions_app:session-start"),
            {"mac_address": self.mac},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        session = Session.objects.get(mac_address=self.mac, status="active")
        self.assertEqual(session.amount_paid, 7)
        self.assertEqual(session.duration_minutes_purchased, 80)
        self.assertEqual(session.plan, self.p5)

        c1.refresh_from_db()
        c2.refresh_from_db()
        c3.refresh_from_db()
        self.assertEqual(c1.session_id, session.id)
        self.assertEqual(c2.session_id, session.id)
        self.assertEqual(c3.session_id, session.id)

        # Device allowed with speed limit from highest plan (p5 = 3 Mbps -> 3072 kbps)
        allow_device_mock.assert_called_once_with(self.mac, rate_kbps=3072, upload_kbps=3072)

    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_session_extend_paid_without_plan_id_and_combo_coins(self, allow_device_mock):
        session = Session.objects.create(
            mac_address=self.mac,
            plan=self.p1,
            duration_minutes_purchased=10,
            amount_paid=1,
            status="active",
        )
        # Drop ₱7 (₱5 + two ₱1)
        CoinEvent.objects.create(amount=5, denomination=5, mac_address=self.mac)
        CoinEvent.objects.create(amount=1, denomination=1, mac_address=self.mac)
        CoinEvent.objects.create(amount=1, denomination=1, mac_address=self.mac)

        response = self.client.post(
            reverse("sessions_app:session-extend-paid"),
            {"mac_address": self.mac},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        # 10 + 80 = 90 minutes
        self.assertEqual(session.duration_minutes_purchased, 90)
        # 1 + 7 = 8 pesos
        self.assertEqual(session.amount_paid, 8)
        # Plan upgraded to p5 (speed always wins: 3 Mbps > 2 Mbps)
        self.assertEqual(session.plan, self.p5)


class SmartComboExamplesTests(TestCase):
    def setUp(self):
        self.p1 = Plan.objects.create(name="₱1 Plan", price=1, duration_minutes=10, is_active=True)
        self.p5 = Plan.objects.create(name="₱5 Plan", price=5, duration_minutes=60, is_active=True)
        self.p10 = Plan.objects.create(name="₱10 Plan", price=10, duration_minutes=150, is_active=True)
        self.p20 = Plan.objects.create(name="₱20 Plan", price=20, duration_minutes=360, is_active=True)

    def test_generate_smart_combos_standard_rates(self):
        from .views import generate_smart_combo_examples
        combos = generate_smart_combo_examples(is_extend=False)
        self.assertEqual(len(combos), 3)

        # ₱7: ₱5 Plan + two ₱1 Plans = 1h 20m
        self.assertEqual(combos[0]["amount"], 7)
        self.assertEqual(combos[0]["breakdown"], "₱5 Plan + two ₱1 Plans")
        self.assertEqual(combos[0]["duration"], "1h 20m")

        # ₱15: ₱10 Plan + ₱5 Plan = 3h 30m
        self.assertEqual(combos[1]["amount"], 15)
        self.assertEqual(combos[1]["breakdown"], "₱10 Plan + ₱5 Plan")
        self.assertEqual(combos[1]["duration"], "3h 30m")

        # ₱25: ₱20 Plan + ₱5 Plan = 7 Hours
        self.assertEqual(combos[2]["amount"], 25)
        self.assertEqual(combos[2]["breakdown"], "₱20 Plan + ₱5 Plan")
        self.assertEqual(combos[2]["duration"], "7 Hours")

    def test_generate_smart_combos_extend_mode(self):
        from .views import generate_smart_combo_examples
        combos = generate_smart_combo_examples(is_extend=True)
        self.assertEqual(len(combos), 3)
        self.assertEqual(combos[0]["duration"], "+1h 20m")
        self.assertEqual(combos[1]["duration"], "+3h 30m")
        self.assertEqual(combos[2]["duration"], "+7 Hours")

    def test_generate_smart_combos_dynamic_custom_rates(self):
        from .views import generate_smart_combo_examples
        Plan.objects.all().delete()
        Plan.objects.create(name="Custom P5", price=5, duration_minutes=30, is_active=True)
        Plan.objects.create(name="Custom P10", price=10, duration_minutes=70, is_active=True)
        combos = generate_smart_combo_examples(is_extend=False)
        self.assertTrue(len(combos) > 0)
        for c in combos:
            self.assertGreater(c["amount"], 0)
            self.assertIn("Plan", c["breakdown"])
            self.assertTrue(len(c["duration"]) > 0)

    def test_generate_smart_combos_empty_plans(self):
        from .views import generate_smart_combo_examples
        Plan.objects.all().delete()
        combos = generate_smart_combo_examples()
        self.assertEqual(combos, [])




