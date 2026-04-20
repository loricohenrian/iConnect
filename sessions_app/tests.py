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

    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_session_start_uses_only_matching_mac_payment(self, allow_device_mock):
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
        allow_device_mock.assert_called_once_with(self.mac_one)

    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_session_start_rejects_unscoped_or_other_device_payment(self, allow_device_mock):
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
            {"amount": 5, "denomination": 5},
            format="json",
        )
        self.assertEqual(no_key.status_code, 401)

        with_key = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 5, "denomination": 5},
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
        first = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 5, "denomination": 5},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )
        self.assertEqual(first.status_code, 201)

        second = self.client.post(
            reverse("sessions_app:coin-inserted"),
            {"amount": 5, "denomination": 5},
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
            {"amount": 10, "denomination": 5},
            format="json",
            HTTP_X_DEVICE_API_KEY="test-device-key",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("amount", response.json())

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
        self.assertEqual(responses["start"], 402)

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
        self.assertEqual(body["speed_mode"], "simulated")

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
        self.assertEqual(body["status"], "no_session")
        self.assertTrue(SuspiciousDevice.objects.filter(mac_address=self.mac_one).exists())

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

        self.assertEqual(response.status_code, 404)
        incident = SuspiciousDevice.objects.get(mac_address=self.mac_one)
        self.assertIn("speed", incident.reason)

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

        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json().get("suspected_clone"))
        self.assertTrue(SuspiciousDevice.objects.filter(mac_address=self.mac_one).exists())
        allow_device_mock.assert_not_called()

    def test_session_status_updates_bandwidth_usage(self):
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
