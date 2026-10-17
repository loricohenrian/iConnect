"""IP refreshes must retain plan caps; all hardware calls use mocks."""
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, TestCase
from rest_framework.test import APIRequestFactory

from dashboard.models import SystemSettings
from .models import CoinEvent, Plan, PurchaseTransaction, Session, SessionGroup
from .views import _session_ip_matches_request, session_start


class IpChangeBandwidthTests(TestCase):
    mac = "02:00:00:00:00:01"
    old_ip = "10.10.10.101"
    new_ip = "10.10.10.102"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        settings_obj = SystemSettings.get_settings()
        settings_obj.enable_internet_check = False
        settings_obj.save(update_fields=["enable_internet_check"])
        self.plan = Plan.objects.create(
            name="IP refresh test", price=10, duration_minutes=120,
            speed_limit=Decimal("6.0"), speed_limit_upload=Decimal("4.0"),
            pause_limit=10,
        )
        self.session = Session.objects.create(
            mac_address=self.mac, ip_address=self.old_ip, plan=self.plan,
            status="active", amount_paid=10, duration_minutes_purchased=120,
            initial_bandwidth_mb=0, pause_limit=10, pause_count=2,
        )
        for name, value in (
            ("sessions_app.views.iptables.allow_device", True),
            ("sessions_app.views.iptables.block_device", True),
            ("sessions_app.views.iptables.enforce_firewall_baseline", True),
            ("sessions_app.views._extract_device_name", "Test device"),
        ):
            patcher = patch(name, return_value=value)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            if name.endswith("allow_device"):
                self.allow = mock
            elif name.endswith("block_device"):
                self.block = mock
            elif name.endswith("enforce_firewall_baseline"):
                self.baseline = mock

    def set_speeds(self, download, upload):
        self.plan.speed_limit = download
        self.plan.speed_limit_upload = upload
        self.plan.save(update_fields=["speed_limit", "speed_limit_upload"])

    def refresh_ip(self, path="sync", new_ip=None, requested_plan=None):
        new_ip = new_ip or self.new_ip
        if path == "sync":
            request = RequestFactory().get("/", REMOTE_ADDR=new_ip)
            self.assertTrue(_session_ip_matches_request(self.session, request))
        else:
            request = APIRequestFactory().post(
                "/api/session/start/",
                {"mac_address": self.mac, "plan_id": (requested_plan or self.plan).pk},
                format="json", REMOTE_ADDR=new_ip,
            )
            response = session_start(request)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data["error"], "Device already has an active session")
            self.baseline.assert_not_called()

    def assert_caps(self, download, upload):
        self.allow.assert_called_once_with(
            self.mac, rate_kbps=download, upload_kbps=upload,
        )
        self.block.assert_not_called()
        self.session.refresh_from_db()
        self.assertEqual(self.session.ip_address, self.new_ip)

    def check_plan_speeds(self, path):
        for download, upload, expected_dl, expected_ul in (
            ("3.0", "1.0", 3072, 1024),
            ("4.0", "2.0", 4096, 2048),
            ("6.0", "4.0", 6144, 4096),
            ("8.0", "6.0", 8192, 6144),
            ("3.5", "1.5", 3584, 1536),
        ):
            with self.subTest(download=download, upload=upload):
                self.set_speeds(Decimal(download), Decimal(upload))
                self.session.ip_address = self.old_ip
                self.session.save(update_fields=["ip_address"])
                self.allow.reset_mock()
                self.refresh_ip(path)
                self.assert_caps(expected_dl, expected_ul)

    def test_sync_converts_each_plan_and_preserves_separate_upload(self):
        self.check_plan_speeds("sync")

    def test_existing_start_converts_each_plan_and_preserves_separate_upload(self):
        self.check_plan_speeds("start")

    def check_fallback_speeds(self, path):
        for download, upload, expected_dl, expected_ul in (
            (Decimal("6.0"), None, 6144, 6144),
            (None, Decimal("1.5"), None, 1536),
            (None, None, None, None),
            (Decimal("0"), Decimal("0"), None, None),
        ):
            with self.subTest(download=download, upload=upload):
                self.set_speeds(download, upload)
                self.session.ip_address = self.old_ip
                self.session.save(update_fields=["ip_address"])
                self.allow.reset_mock()
                self.refresh_ip(path)
                self.assert_caps(expected_dl, expected_ul)

    def test_sync_preserves_existing_default_speed_conventions(self):
        self.check_fallback_speeds("sync")

    def test_existing_start_preserves_existing_default_speed_conventions(self):
        self.check_fallback_speeds("start")

    def test_sync_with_no_plan_keeps_global_defaults(self):
        self.session.plan = None
        self.session.save(update_fields=["plan"])
        self.refresh_ip()
        self.assert_caps(None, None)

    def test_existing_planless_group_keeps_global_defaults(self):
        group = SessionGroup.objects.create(
            group_code="IPTEST", max_devices=2, total_price=20,
            duration_minutes=120,
        )
        self.session.plan = None
        self.session.session_group = group
        self.session.save(update_fields=["plan", "session_group"])
        self.refresh_ip("start")
        self.assert_caps(None, None)

    def test_existing_start_uses_current_plan_not_requested_plan(self):
        other_plan = Plan.objects.create(
            name="Other plan", price=20, duration_minutes=300,
            speed_limit=8, speed_limit_upload=6,
        )
        self.refresh_ip("start", requested_plan=other_plan)
        self.assert_caps(6144, 4096)
        self.assertEqual(self.session.plan_id, self.plan.pk)

    def test_unchanged_ip_does_not_reapply_access(self):
        for path in ("sync", "start"):
            with self.subTest(path=path):
                self.refresh_ip(path, new_ip=self.old_ip)
                self.allow.assert_not_called()
                self.block.assert_not_called()

    def test_sync_does_not_grant_access_to_paused_or_expired_sessions(self):
        for state in ("paused", "expired"):
            with self.subTest(state=state):
                self.session.status = state
                self.session.ip_address = self.old_ip
                self.session.save(update_fields=["status", "ip_address"])
                self.refresh_ip()
                self.allow.assert_not_called()
                self.session.refresh_from_db()
                self.assertEqual(self.session.status, state)

    def test_sync_without_session_does_not_grant_access(self):
        self.assertFalse(_session_ip_matches_request(None, RequestFactory().get("/")))
        self.allow.assert_not_called()

    def test_ip_refresh_changes_no_time_payments_pause_or_usage_fields(self):
        for path in ("sync", "start"):
            with self.subTest(path=path):
                self.session.ip_address = self.old_ip
                self.session.save(update_fields=["ip_address"])
                before = Session.objects.values().get(pk=self.session.pk)
                self.allow.reset_mock()
                self.refresh_ip(path)
                after = Session.objects.values().get(pk=self.session.pk)
                self.assertEqual(after.pop("ip_address"), self.new_ip)
                before.pop("ip_address")
                self.assertEqual(before, after)
                self.assertEqual(Session.objects.count(), 1)
                self.assertFalse(CoinEvent.objects.exists())
                self.assertFalse(PurchaseTransaction.objects.exists())
