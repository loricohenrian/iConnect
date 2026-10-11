"""Capacity regressions use isolated test records and mocked hardware only."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework.test import APIClient

from dashboard.models import SystemSettings
from .capacity import NETWORK_FULL_MESSAGE, resume_admin_session
from .models import CoinEvent, CoinInsertRequest, Plan, Session, SuspiciousDevice
from .views import _activate_next_coin_request, _coin_request_payload


class CapacityTemplateTests(SimpleTestCase):
    def test_initial_full_network_notice_and_active_extension_exception(self):
        from types import SimpleNamespace
        import re
        for state, hidden in (("paused", False), ("active", True)):
            html = render_to_string("base_portal.html", {"slots_available": 0,
                "slots_max": 50, "slots_active": 50, "session": SimpleNamespace(status=state)})
            tag = re.search(r'<div id="network-full-notice"[^>]*>', html).group()
            self.assertEqual("hidden" in tag, hidden)
            self.assertIn('data-network-full="true"', html)
            self.assertIn(f'data-session-active="{str(state == "active").lower()}"', html)

    def test_full_network_notice_hidden_when_space_is_available(self):
        import re
        html = render_to_string("base_portal.html", {"slots_available": 1})
        self.assertIn("hidden", re.search(r'<div id="network-full-notice"[^>]*>', html).group())


class CapacityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.settings = SystemSettings.get_settings()
        self.settings.max_concurrent_sessions = 1
        self.settings.enable_internet_check = False
        self.settings.save()
        self.plan = Plan.objects.create(name="Capacity test", price=5, duration_minutes=60)
        self.now = timezone.now()
        self.active_mac = "AA:BB:CC:DD:EE:01"
        self.new_mac = "AA:BB:CC:DD:EE:02"
        self.active = self.make_session(self.active_mac)
        for name, value in [
            ("sessions_app.power_recovery.ensure_power_recovery", None),
            ("sessions_app.internet_monitor.check_isp_internet_status", {"isp_outage": False}),
            ("sessions_app.iptables.enforce_firewall_baseline", True),
            ("sessions_app.iptables.allow_device", True),
            ("sessions_app.iptables.block_device", True),
        ]:
            mocker = patch(name, return_value=value)
            mock = mocker.start()
            self.addCleanup(mocker.stop)
            if name.endswith("allow_device"):
                self.allow = mock
            if name.endswith("block_device"):
                self.block = mock

    def make_session(self, mac, status="active", **extra):
        fields = dict(mac_address=mac, plan=self.plan, ip_address="10.10.10.168",
                      status=status, time_in=self.now, duration_minutes_purchased=60,
                      amount_paid=5, initial_bandwidth_mb=0)
        if status == "paused":
            fields["paused_at"] = self.now
        fields.update(extra)
        return Session.objects.create(**fields)

    def coin_request(self, mac, status=CoinInsertRequest.STATUS_PENDING):
        return CoinInsertRequest.objects.create(mac_address=mac, ip_address="10.10.10.168",
            plan=self.plan, expected_amount=5, status=status,
            activated_at=self.now if status == CoinInsertRequest.STATUS_ACTIVE else None,
            expires_at=self.now + timedelta(minutes=2) if status == CoinInsertRequest.STATUS_ACTIVE else None)

    def post(self, path, mac=None, **extra):
        return self.client.post(path, {"mac_address": mac or self.new_mac,
                                     "plan_id": self.plan.pk, **extra}, format="json")

    def test_full_blocks_new_coin_request_without_creating_queue_record(self):
        response = self.post("/api/session/start/request/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"], NETWORK_FULL_MESSAGE)
        self.assertFalse(CoinInsertRequest.objects.exists())

    def test_full_blocks_paused_customer_without_changing_balance(self):
        paused = self.make_session(self.new_mac, "paused")
        before = paused.time_remaining_seconds
        self.assertEqual(self.post("/api/session/start/request/").status_code, 503)
        paused.refresh_from_db()
        self.assertEqual(paused.status, "paused")
        self.assertEqual(paused.time_remaining_seconds, before)

    def test_active_customer_can_request_coins_when_full(self):
        response = self.post("/api/session/start/request/", self.active_mac.lower())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["coin_request"]["status"], "active")
        self.assertFalse(response.data["coin_request"]["network_full"])

    def test_request_opens_after_slot_becomes_available(self):
        self.active.status = "expired"
        self.active.save(update_fields=["status"])
        self.assertEqual(self.post("/api/session/start/request/").status_code, 201)

    def test_full_suspends_open_coin_window_without_losing_credits(self):
        req = self.coin_request(self.new_mac, CoinInsertRequest.STATUS_ACTIVE)
        req.credited_amount = 5
        req.save(update_fields=["credited_amount"])
        self.assertIsNone(_activate_next_coin_request())
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")
        self.assertEqual(req.credited_amount, 5)
        self.assertIsNone(req.expires_at)
        payload = _coin_request_payload(req)
        self.assertTrue(payload["network_full"])
        self.assertFalse(payload["ready_to_start"])

    def test_relay_is_disabled_for_full_network_new_customer(self):
        self.coin_request(self.new_mac, CoinInsertRequest.STATUS_ACTIVE)
        response = self.client.get("/api/coinslot/status/")
        self.assertFalse(response.data["enabled"])

    def test_active_extension_is_not_stuck_behind_capacity_blocked_request(self):
        waiting = self.coin_request(self.new_mac)
        extending = self.coin_request(self.active_mac)
        self.assertEqual(_activate_next_coin_request().pk, extending.pk)
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "pending")
        self.assertTrue(self.client.get("/api/coinslot/status/").data["enabled"])

    def test_suspended_request_reopens_when_slot_frees(self):
        waiting = self.coin_request(self.new_mac, CoinInsertRequest.STATUS_ACTIVE)
        _activate_next_coin_request()
        self.active.status = "expired"
        self.active.save(update_fields=["status"])
        self.assertEqual(_activate_next_coin_request().pk, waiting.pk)
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "active")
        self.assertIsNotNone(waiting.expires_at)

    def test_paid_activation_cannot_bypass_full_network(self):
        coin = CoinEvent.objects.create(mac_address=self.new_mac, amount=5, denomination=5)
        for endpoint in ("/api/session/start/", "/api/session/extend-paid/"):
            self.assertEqual(self.post(endpoint).status_code, 503)
        coin.refresh_from_db()
        self.assertIsNone(coin.session_id)
        self.assertFalse(Session.objects.filter(mac_address=self.new_mac).exists())
        self.allow.assert_not_called()

    def test_voucher_activation_cannot_bypass_full_network(self):
        voucher = self.make_session(self.new_mac, "paused", voucher_code="CAPACITY")
        response = self.post("/api/session/extend/", voucher_code="CAPACITY")
        self.assertEqual(response.status_code, 503)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, "paused")
        self.allow.assert_not_called()

    def test_existing_active_customer_can_extend_paid_at_capacity(self):
        CoinEvent.objects.create(mac_address=self.active_mac, amount=5, denomination=5)
        response = self.post("/api/session/extend-paid/", self.active_mac)
        self.assertEqual(response.status_code, 200)
        self.active.refresh_from_db()
        self.assertEqual(self.active.duration_minutes_purchased, 120)
        self.assertEqual(Session.objects.filter(status="active").count(), 1)

    def test_live_capacity_exception_uses_active_session_not_newer_voucher(self):
        self.make_session(self.active_mac, "paused", voucher_code="NEWER", paused_at=None)
        with patch("portal.views._get_mac_address", return_value=self.active_mac):
            response = self.client.get("/api/portal/live-data/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["device_session_active"])

    def test_paused_customer_cannot_extend_paid_when_full(self):
        paused = self.make_session(self.new_mac, "paused")
        coin = CoinEvent.objects.create(mac_address=self.new_mac, amount=5, denomination=5)
        self.assertEqual(self.post("/api/session/extend-paid/").status_code, 503)
        paused.refresh_from_db()
        coin.refresh_from_db()
        self.assertEqual(paused.duration_minutes_purchased, 60)
        self.assertEqual(paused.status, "paused")
        self.assertIsNone(coin.session_id)

    def test_full_blocks_resume_but_allows_active_user_to_pause(self):
        paused = self.make_session(self.new_mac, "paused")
        self.assertEqual(self.post("/api/session/pause/").status_code, 503)
        self.assertEqual(self.post("/api/session/pause/", self.active_mac).status_code, 200)
        self.assertEqual(self.post("/api/session/pause/").status_code, 200)
        paused.refresh_from_db()
        self.assertEqual(paused.status, "active")

    def admin_login(self):
        admin = get_user_model().objects.create_user("capacity_admin", is_staff=True)
        self.client.force_login(admin)

    def test_resume_all_40_plus_20_caps_at_50_and_reports_skips(self):
        self.admin_login()
        self.settings.max_concurrent_sessions = 50
        self.settings.save()
        for i in range(2, 41):
            self.make_session(f"AA:00:00:00:00:{i:02X}")
        paused = [self.make_session(f"BB:00:00:00:00:{i:02X}", "paused") for i in range(20)]
        response = self.client.post("/iconnect-ops/sessions/resume-all/")
        data = response.json()
        self.assertEqual(data["resumed_count"], 10)
        self.assertEqual(data["skipped_capacity_count"], 10)
        self.assertEqual(data["message"], "Resumed 10 sessions. Skipped 10 because the network reached its 50-user limit.")
        self.assertEqual(Session.objects.filter(status="active").count(), 50)
        for item in paused[10:]:
            item.refresh_from_db()
            self.assertEqual(item.status, "paused")
            self.assertEqual(item.time_remaining_seconds, 3600)
        self.assertEqual(self.allow.call_count, 10)

    def test_resume_all_when_already_full_does_not_spend_time(self):
        self.admin_login()
        self.make_session(self.new_mac, "paused")
        data = self.client.post("/iconnect-ops/sessions/resume-all/").json()
        self.assertEqual(data["resumed_count"], 0)
        self.assertEqual(data["skipped_capacity_count"], 1)
        self.allow.assert_not_called()

    def test_resume_all_skips_blacklist_and_finalizes_expired_separately(self):
        self.admin_login()
        self.settings.max_concurrent_sessions = 2
        self.settings.save()
        blocked = self.make_session(self.new_mac, "paused")
        SuspiciousDevice.objects.create(mac_address=blocked.mac_address, is_blocked=True)
        expired = self.make_session("BB:BB:BB:BB:BB:03", "paused", duration_minutes_purchased=0)
        good = self.make_session("BB:BB:BB:BB:BB:04", "paused")
        data = self.client.post("/iconnect-ops/sessions/resume-all/").json()
        self.assertEqual(data["resumed_count"], 1)
        self.assertEqual(data["skipped_blocked_count"], 1)
        self.assertEqual(data["expired_count"], 1)
        good.refresh_from_db()
        expired.refresh_from_db()
        self.assertEqual(good.status, "active")
        self.assertEqual(expired.status, "expired")

    def test_failed_grant_preserves_balance_and_leaves_slot_for_next_customer(self):
        self.admin_login()
        self.settings.max_concurrent_sessions = 2
        self.settings.save()
        first = self.make_session(self.new_mac, "paused")
        second = self.make_session("BB:BB:BB:BB:BB:03", "paused")
        self.allow.side_effect = [False, True]
        data = self.client.post("/iconnect-ops/sessions/resume-all/").json()
        self.assertEqual(data["failed_count"], 1)
        self.assertEqual(data["resumed_count"], 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "paused")
        self.assertEqual(first.time_remaining_seconds, 3600)
        self.assertEqual(second.status, "active")

    def test_admin_resume_uses_saved_limit_not_a_hardcoded_50(self):
        paused = self.make_session(self.new_mac, "paused")
        self.assertEqual(resume_admin_session(paused.pk), "full")
        self.settings.max_concurrent_sessions = 2
        self.settings.save()
        self.assertEqual(resume_admin_session(paused.pk), "resumed")

    def test_power_paused_session_waits_for_capacity(self):
        paused = self.make_session(self.new_mac, "paused", power_paused=True,
                                   power_remaining_seconds=3600)
        self.assertEqual(resume_admin_session(paused.pk), "full")
        paused.refresh_from_db()
        self.assertTrue(paused.power_paused)
        self.assertEqual(paused.time_remaining_seconds, 3600)

    def test_failed_resume_save_rolls_back_time_and_revokes_grant(self):
        self.settings.max_concurrent_sessions = 2
        self.settings.save()
        paused = self.make_session(self.new_mac, "paused")
        with patch.object(Session, "resume_session", side_effect=RuntimeError("test save failed")):
            self.assertEqual(resume_admin_session(paused.pk), "failed")
        paused.refresh_from_db()
        self.assertEqual(paused.status, "paused")
        self.assertEqual(paused.time_remaining_seconds, 3600)
        self.block.assert_called_once_with(self.new_mac)

    def test_admin_resume_grants_access_before_starting_timer(self):
        self.settings.max_concurrent_sessions = 2
        self.settings.save()
        paused = self.make_session(self.new_mac, "paused")
        def inspect_grant(*args, **kwargs):
            paused.refresh_from_db()
            self.assertEqual(paused.status, "paused")
            return True
        self.allow.side_effect = inspect_grant
        self.assertEqual(resume_admin_session(paused.pk), "resumed")
