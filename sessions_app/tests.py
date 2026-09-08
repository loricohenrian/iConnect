from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import CoinEvent, CoinInsertRequest, Plan, Session, SessionGroup, WhitelistedDevice, SuspiciousDevice


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

    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=True)
    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_group_pass_overpayment_charges_exact_and_credits_balance(self, allow_mock, base_mock):
        p1_plan = Plan.objects.create(name="P1 Plan", price=1, duration_minutes=15, is_active=True)
        # User inserts ₱5 for a 2-person group pass (expected ₱1 x 2 = ₱2)
        CoinEvent.objects.create(
            amount=5,
            denomination=5,
            mac_address=self.mac_one,
        )
        response = self.client.post(
            reverse("sessions_app:session-start"),
            {
                "mac_address": self.mac_one,
                "plan_id": p1_plan.id,
                "is_group_pass": True,
                "group_pass_devices": 2,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        session = Session.objects.get(mac_address=self.mac_one, status="active")
        self.assertEqual(session.duration_minutes_purchased, 15)
        self.assertEqual(session.amount_paid, 2)
        group = session.session_group
        self.assertIsNotNone(group)
        self.assertEqual(group.duration_minutes, 15)
        self.assertEqual(group.total_price, 2)
        self.assertEqual(group.max_devices, 2)
        self.assertEqual(group.redeemed_count, 1)

        # Excess ₱3 returned to user balance as unlinked CoinEvent
        unlinked_coins = CoinEvent.objects.filter(mac_address=self.mac_one, session=None).aggregate(total=Sum("amount"))["total"]
        self.assertEqual(unlinked_coins, 3)

    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=True)
    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_group_pass_extension_overpayment_charges_exact_and_credits_balance(self, allow_mock, base_mock):
        p1_plan = Plan.objects.create(name="P1 Plan", price=1, duration_minutes=15, is_active=True)
        active_session = Session.objects.create(
            mac_address=self.mac_one,
            plan=p1_plan,
            duration_minutes_purchased=15,
            amount_paid=1,
            status="active",
        )
        CoinEvent.objects.create(
            amount=5,
            denomination=5,
            mac_address=self.mac_one,
        )
        response = self.client.post(
            reverse("sessions_app:session-extend-paid"),
            {
                "mac_address": self.mac_one,
                "plan_id": p1_plan.id,
                "is_group_pass": True,
                "group_devices": 2,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        active_session.refresh_from_db()
        self.assertEqual(active_session.duration_minutes_purchased, 30) # 15 original + 15 extend
        self.assertEqual(active_session.amount_paid, 3) # 1 original + 2 group pass
        group = active_session.session_group
        self.assertIsNotNone(group)
        self.assertEqual(group.duration_minutes, 15)
        self.assertEqual(group.total_price, 2)
        self.assertEqual(group.max_devices, 2)
        self.assertEqual(group.redeemed_count, 1)

        unlinked_coins = CoinEvent.objects.filter(mac_address=self.mac_one, session=None).aggregate(total=Sum("amount"))["total"]
        self.assertEqual(unlinked_coins, 3)

    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=True)
    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_group_pass_join_redemption_flow(self, allow_mock, base_mock):
        p1_plan = Plan.objects.create(name="P1 Plan", price=1, duration_minutes=15, is_active=True)
        CoinEvent.objects.create(amount=2, denomination=1, mac_address=self.mac_one)
        start_res = self.client.post(
            reverse("sessions_app:session-start"),
            {
                "mac_address": self.mac_one,
                "plan_id": p1_plan.id,
                "is_group_pass": True,
                "group_pass_devices": 2,
            },
            format="json",
        )
        self.assertEqual(start_res.status_code, 201)
        group_code = start_res.data["session_group"]

        # Phone 2 joins with code and blank device_name (common portal scenario)
        join_res = self.client.post(
            reverse("sessions_app:session-join-group"),
            {
                "mac_address": self.mac_two,
                "group_code": group_code,
                "device_name": "",
            },
            format="json",
        )
        self.assertEqual(join_res.status_code, 201)
        session_two = Session.objects.get(mac_address=self.mac_two, status="active")
        self.assertEqual(session_two.duration_minutes_purchased, 15)

        # Group is now exhausted
        group = SessionGroup.objects.get(group_code=group_code)
        self.assertEqual(group.redeemed_count, 2)
        self.assertEqual(group.status, "exhausted")

        # Phone 2 attempts to join again -> rejected duplicate (409)
        dup_res = self.client.post(
            reverse("sessions_app:session-join-group"),
            {
                "mac_address": self.mac_two,
                "group_code": group_code,
            },
            format="json",
        )
        self.assertEqual(dup_res.status_code, 409)
        self.assertIn("already redeemed", dup_res.data["error"].lower())

        # Phone 3 attempts to join -> rejected full (400)
        mac_three = "AA:BB:CC:DD:EE:03"
        full_res = self.client.post(
            reverse("sessions_app:session-join-group"),
            {
                "mac_address": mac_three,
                "group_code": group_code,
            },
            format="json",
        )
        self.assertEqual(full_res.status_code, 400)
        self.assertIn("full", full_res.data["error"].lower())

    @patch("sessions_app.views._mac_from_arp")
    @patch("sessions_app.views.iptables.enforce_firewall_baseline", return_value=True)
    @patch("sessions_app.views.iptables.allow_device", return_value=True)
    def test_group_pass_join_arp_mac_resolution(self, allow_mock, base_mock, arp_mock):
        arp_mock.return_value = self.mac_two
        p1_plan = Plan.objects.create(name="P1 Plan ARP", price=1, duration_minutes=15, is_active=True)
        CoinEvent.objects.create(amount=2, denomination=1, mac_address=self.mac_one)
        start_res = self.client.post(
            reverse("sessions_app:session-start"),
            {
                "mac_address": self.mac_one,
                "plan_id": p1_plan.id,
                "is_group_pass": True,
                "group_pass_devices": 2,
            },
            format="json",
        )
        self.assertEqual(start_res.status_code, 201)
        group_code = start_res.data["session_group"]

        # Phone 2 joins with only group_code (no mac provided, resolved via ARP)
        join_res = self.client.post(
            reverse("sessions_app:session-join-group"),
            {
                "group_code": group_code,
                "device_name": "",
            },
            format="json",
        )
        self.assertEqual(join_res.status_code, 201)
        self.assertTrue(Session.objects.filter(mac_address=self.mac_two, status="active").exists())

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

    @patch("sessions_app.bandwidth.get_device_bandwidth_mb", side_effect=[0.0, 5.0])
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

    @patch("sessions_app.bandwidth.get_device_bandwidth_mb")
    def test_consecutive_sessions_start_at_zero_bandwidth(self, mock_bandwidth):
        from sessions_app.bandwidth import refresh_session_bandwidth_usage

        # Session 1 starts with 0 hardware counters, uses 66.1 MB
        mock_bandwidth.return_value = 0.0
        sess1 = Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            ip_address="127.0.0.1",
        )
        mock_bandwidth.return_value = 66.1
        refresh_session_bandwidth_usage(sess1)
        sess1.refresh_from_db()
        self.assertEqual(sess1.bandwidth_used_mb, 66.1)
        sess1.status = "expired"
        sess1.save(update_fields=["status"])

        # Session 2 starts for the same device when hardware counter is 66.1 MB
        sess2 = Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            ip_address="127.0.0.1",
        )
        self.assertEqual(sess2.initial_bandwidth_mb, 66.1)

        # Before device transmits any new data, hardware counter is 66.1 MB
        refresh_session_bandwidth_usage(sess2)
        sess2.refresh_from_db()
        self.assertEqual(sess2.bandwidth_used_mb, 0.0)

        # Device uses 2.5 MB (hardware counter reaches 68.6 MB)
        mock_bandwidth.return_value = 68.6
        refresh_session_bandwidth_usage(sess2)
        sess2.refresh_from_db()
        self.assertEqual(sess2.bandwidth_used_mb, 2.5)

    @patch("sessions_app.bandwidth.get_device_bandwidth_mb", return_value=66.2)
    def test_retroactive_baseline_correction_for_existing_session(self, mock_bandwidth):
        from sessions_app.bandwidth import refresh_session_bandwidth_usage

        # Session 1 expired with 66.1 MB
        sess1 = Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="expired",
            bandwidth_used_mb=66.1,
            initial_bandwidth_mb=0,
        )
        # Session 2 was created before baseline tracking (initial_bandwidth_mb=0, bandwidth_used_mb=66.2)
        sess2 = Session.objects.create(
            mac_address=self.mac_one,
            plan=self.plan,
            duration_minutes_purchased=self.plan.duration_minutes,
            amount_paid=self.plan.price,
            status="active",
            bandwidth_used_mb=66.2,
            initial_bandwidth_mb=0,
        )
        # Refresh should detect baseline from sess1 and correct sess2 to 0.1 MB
        refresh_session_bandwidth_usage(sess2)
        sess2.refresh_from_db()
        self.assertEqual(sess2.initial_bandwidth_mb, 66.1)
        self.assertEqual(sess2.bandwidth_used_mb, 0.1)

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


class DeviceNameDetectionTests(TestCase):
    def test_extract_device_name_from_dhcp_lease(self):
        import tempfile
        from unittest.mock import patch
        from .views import _extract_device_name

        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write("1788674820 d4:17:61:bc:b4:a7 10.10.10.34 POCO-X7-Pro 01:d4:17:61:bc:b4:a7\n")
            f.flush()
            with override_settings(DNSMASQ_LEASES_FILE=f.name):
                # Should detect POCO-X7-Pro even if generic name is passed
                name = _extract_device_name(None, passed_name="Android Phone", mac_address="D4:17:61:BC:B4:A7")
                self.assertEqual(name, "POCO-X7-Pro")

    def test_extract_device_name_from_user_agent_infinix(self):
        from unittest.mock import MagicMock
        from .views import _extract_device_name

        request = MagicMock()
        request.META = {
            "HTTP_USER_AGENT": "Mozilla/5.0 (Linux; Android 14; Infinix X6725 Build/AP3A.240905.015; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/128.0.6613.88 Mobile Safari/537.36"
        }
        name = _extract_device_name(request, passed_name="Android Phone", mac_address="AA:BB:CC:DD:EE:99")
        self.assertEqual(name, "Infinix X6725 Build/AP3A.240905.015")

    def test_extract_device_name_preserves_custom_admin_name(self):
        from .views import _extract_device_name
        name = _extract_device_name(None, passed_name="Henrian Laptop", mac_address="AA:BB:CC:DD:EE:99")
        self.assertEqual(name, "Henrian Laptop")


class SessionEarlyEndAndTimezoneTests(TestCase):
    def test_extend_session_on_expired_session_resets_timeout_and_pause(self):
        from datetime import timedelta
        session = Session.objects.create(
            mac_address="11:22:33:44:55:66",
            duration_minutes_purchased=30,
            amount_paid=2,
            status="expired",
            time_in=timezone.now() - timedelta(minutes=45),
            time_out=timezone.now() - timedelta(minutes=15),
            total_paused_seconds=120,
            paused_at=timezone.now() - timedelta(minutes=20),
        )
        self.assertEqual(session.status, "expired")
        self.assertIsNotNone(session.time_out)

        session.extend_session(15)
        session.save()

        self.assertEqual(session.status, "active")
        self.assertIsNone(session.time_out)
        self.assertEqual(session.total_paused_seconds, 0)
        self.assertIsNone(session.paused_at)
        self.assertEqual(session.duration_minutes_purchased, 15)

    def test_is_ended_early_detection(self):
        from datetime import timedelta
        now = timezone.now()
        # Session 1: Naturally expired 45m session
        s_natural = Session.objects.create(
            mac_address="11:22:33:44:55:66",
            duration_minutes_purchased=45,
            amount_paid=3,
            status="expired",
            time_in=now - timedelta(minutes=45),
            time_out=now,
        )
        self.assertFalse(s_natural.is_ended_early)
        self.assertEqual(s_natural.actual_elapsed_minutes, 45)

        # Session 2: Manually disconnected after 31m of a 45m session
        s_early = Session.objects.create(
            mac_address="22:33:44:55:66:77",
            duration_minutes_purchased=45,
            amount_paid=3,
            status="expired",
            time_in=now - timedelta(minutes=45),
            time_out=now - timedelta(minutes=14),  # Ended 31m after time_in
        )
        self.assertTrue(s_early.is_ended_early)
        self.assertEqual(s_early.actual_elapsed_minutes, 31)

    def test_timezone_middleware_activates_asia_manila(self):
        from django.test import RequestFactory
        from pisowifi.middleware import TimezoneMiddleware
        from django.template import Template, Context
        import datetime

        factory = RequestFactory()
        request = factory.get("/dashboard/sessions/")

        # Deactivate or set to UTC beforehand
        timezone.activate("UTC")

        middleware = TimezoneMiddleware(lambda req: "OK")
        response = middleware(request)

        self.assertEqual(response, "OK")
        self.assertEqual(timezone.get_current_timezone_name(), "Asia/Manila")

        # Verify aware UTC datetime renders in Asia/Manila (+08:00)
        dt_utc = datetime.datetime(2026, 9, 7, 8, 26, 0, tzinfo=datetime.timezone.utc)
        t = Template('{{ dt|date:"M d, h:i A" }}')
        rendered = t.render(Context({"dt": dt_utc}))
        self.assertEqual(rendered, "Sep 07, 04:26 PM")


class BandwidthTrackingTests(TestCase):
    @patch("sessions_app.bandwidth._is_simulation", return_value=False)
    @patch("sessions_app.bandwidth.subprocess.run")
    @patch("sessions_app.bandwidth._get_mac_to_ip_map")
    @patch("sessions_app.bandwidth._get_ip_to_mac_map")
    def test_get_iptables_byte_counters_aggregates_upload_and_download(
        self, ip_to_mac_mock, mac_to_ip_mock, subprocess_mock, sim_mock
    ):
        from sessions_app.bandwidth import get_iptables_byte_counters, get_device_bandwidth_mb
        import subprocess

        mac = "AA:BB:CC:DD:EE:99"
        ip = "10.10.10.99"
        mac_to_ip_mock.return_value = {mac: ip}
        ip_to_mac_mock.return_value = {ip: mac}

        # Mock iptables FORWARD (Upload: 11.5 MB = 12058624 bytes)
        forward_stdout = (
            "Chain FORWARD (policy DROP 0 packets, 0 bytes)\n"
            f" pkts bytes target prot opt in out source destination\n"
            f" 1000 12058624 ACCEPT all -- * * 0.0.0.0/0 0.0.0.0/0 MAC {mac}\n"
        )

        # Mock iptables mangle POSTROUTING (Download: 145 MB = 152043520 bytes)
        mangle_stdout = (
            "Chain POSTROUTING (policy ACCEPT 0 packets, 0 bytes)\n"
            f" pkts bytes target prot opt in out source destination\n"
            f" 50000 152043520 MARK all -- * * 0.0.0.0/0 {ip} MARK set 0x63\n"
        )

        def mock_subprocess_run(cmd, *args, **kwargs):
            if "mangle" in cmd:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=mangle_stdout)
            elif "FORWARD" in cmd:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=forward_stdout)
            elif "tc" in cmd:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="")

        subprocess_mock.side_effect = mock_subprocess_run

        counters = get_iptables_byte_counters()
        self.assertIn(mac, counters)
        # Expected: 12058624 + 152043520 = 164102144 bytes (~156.5 MB)
        self.assertEqual(counters[mac], 164102144)
        mb = get_device_bandwidth_mb(mac)
        self.assertAlmostEqual(mb, 156.5, places=1)

    def test_session_extend_increases_pause_limit_and_pauses_left(self):
        """Test that extending an active session increases pause limit and pauses left."""
        mac = "AA:BB:CC:DD:EE:01"
        plan1 = Plan.objects.create(name="Plan 1", price=5, duration_minutes=30, pause_limit=2)
        plan2 = Plan.objects.create(name="Plan 2", price=5, duration_minutes=30, pause_limit=3)

        session = Session.objects.create(
            mac_address=mac,
            plan=plan1,
            time_in=timezone.now(),
            duration_minutes_purchased=30,
            amount_paid=5,
            status="active",
        )
        self.assertEqual(session.effective_pause_limit, 2)
        self.assertEqual(session.pauses_left, 2)

        # Simulate user pausing once
        session.pause_session()
        self.assertEqual(session.pause_count, 1)
        self.assertEqual(session.pauses_left, 1)
        session.resume_session()

        # Add coins to pay for plan2
        CoinEvent.objects.create(
            mac_address=mac,
            amount=5,
            denomination=5,
        )

        response = self.client.post(
            reverse("sessions_app:session-extend-paid"),
            {"mac_address": mac, "plan_id": plan2.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "success")

        session.refresh_from_db()
        # Initial limit was 2, plan2 added 3 pauses -> total pause limit = 5
        self.assertEqual(session.effective_pause_limit, 5)
        # Paused once, so pauses left = 5 - 1 = 4
        self.assertEqual(session.pauses_left, 4)
        self.assertEqual(response.data["pauses_left"], 4)

        # Verify session-status endpoint reflects this
        status_res = self.client.get(
            reverse("sessions_app:session-status"),
            {"mac_address": mac},
        )
        self.assertEqual(status_res.status_code, 200)
        self.assertEqual(status_res.data["pauses_left"], 4)

    def test_one_peso_extend_does_not_grant_extra_pauses(self):
        """Test that extending with ₱1 (below minimum threshold) adds time but grants 0 extra pauses."""
        from dashboard.models import SystemSettings
        sys_settings = SystemSettings.get_settings()
        sys_settings.min_extend_amount_for_pause = 5
        sys_settings.save()

        mac = "AA:BB:CC:DD:EE:02"
        plan5 = Plan.objects.create(name="P5", price=5, duration_minutes=30, pause_limit=2)
        plan1 = Plan.objects.create(name="P1", price=1, duration_minutes=15, pause_limit=1)

        session = Session.objects.create(
            mac_address=mac,
            plan=plan5,
            time_in=timezone.now(),
            duration_minutes_purchased=30,
            amount_paid=5,
            status="active",
        )
        self.assertEqual(session.pauses_left, 2)

        # User inserts ₱1 coin to extend
        CoinEvent.objects.create(
            mac_address=mac,
            amount=1,
            denomination=1,
        )

        response = self.client.post(
            reverse("sessions_app:session-extend-paid"),
            {"mac_address": mac, "plan_id": plan1.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        session.refresh_from_db()
        # Time purchased increased by 15 mins (30 + 15 = 45)
        self.assertEqual(session.duration_minutes_purchased, 45)
        # But pauses_left is STILL 2 (no cheap ₱1 pause exploit allowed!)
        self.assertEqual(session.pauses_left, 2)
        self.assertEqual(response.data["pauses_left"], 2)

    def test_pause_cap_enforced_on_extend(self):
        """Test that pauses cannot exceed max_session_pause_cap."""
        from dashboard.models import SystemSettings
        sys_settings = SystemSettings.get_settings()
        sys_settings.min_extend_amount_for_pause = 5
        sys_settings.max_session_pause_cap = 5
        sys_settings.save()

        mac = "AA:BB:CC:DD:EE:03"
        plan_big = Plan.objects.create(name="Big Plan", price=5, duration_minutes=60, pause_limit=4)

        session = Session.objects.create(
            mac_address=mac,
            plan=plan_big,
            time_in=timezone.now(),
            duration_minutes_purchased=60,
            amount_paid=5,
            status="active",
        )
        self.assertEqual(session.pauses_left, 4)

        # Extend with another 4-pause plan: 4 + 4 = 8, but capped at 5!
        CoinEvent.objects.create(
            mac_address=mac,
            amount=5,
            denomination=5,
        )

        response = self.client.post(
            reverse("sessions_app:session-extend-paid"),
            {"mac_address": mac, "plan_id": plan_big.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        session.refresh_from_db()
        self.assertEqual(session.pauses_left, 5)  # Capped at 5!
        self.assertEqual(response.data["pauses_left"], 5)


