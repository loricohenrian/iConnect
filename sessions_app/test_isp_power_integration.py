"""Exercise real ISP monitoring together with durable power-loss recovery."""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from dashboard.models import SystemSettings
from . import internet_monitor as monitor
from .models import Plan, Session, SessionPowerState
from .power_recovery import ensure_power_recovery
from .tasks import auto_resume_connected_sessions


@override_settings(PISONET_GPIO_SIMULATION=False)
class ISPPowerIntegrationTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 9, 9, 50, tzinfo=dt_timezone.utc)
        self.clock = self.mock('django.utils.timezone.now', return_value=self.now)
        self.boot = self.mock('sessions_app.power_recovery.current_boot_id', return_value='boot-a')
        self.allow = self.mock('sessions_app.iptables.allow_device', return_value=True)
        self.block = self.mock('sessions_app.iptables.block_device', return_value=True)
        self.probe = self.mock('sessions_app.internet_monitor.probe_upstream_internet', return_value=True)
        self.mock('dashboard.telegram_bot.get_telegram_config', return_value={'enabled': False})
        self.plan = Plan.objects.create(name='Integration plan', price=15, duration_minutes=180,
                                        pause_duration_limit=1, pause_limit=1)
        settings = SystemSettings.get_settings()
        settings.enable_internet_check = True
        settings.enable_outage_auto_pause = True
        settings.enable_outage_announcement = True
        settings.enable_auto_pause_resume = True
        settings.save()
        SessionPowerState.objects.create(pk=1, boot_id='boot-a', checkpoint_at=self.now)
        cache.clear()
        self.addCleanup(cache.clear)

    def mock(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def session(self, mac='46:D3:34:7C:62:61'):
        return Session.objects.create(mac_address=mac, plan=self.plan, amount_paid=15,
                                      duration_minutes_purchased=180, status='active',
                                      time_in=self.clock.return_value - timedelta(minutes=10),
                                      ip_address='10.10.10.178', initial_bandwidth_mb=0)

    def mark_outage(self, *sessions):
        cache.set(monitor.CACHE_KEY_ACTIVE_OUTAGE, True)
        cache.set(monitor.CACHE_KEY_PAUSED_IDS, [s.pk for s in sessions])
        for s in sessions:
            cache.set(f'outage_paused_{s.pk}', True)
            cache.set(f'manual_pause_{s.pk}', True)

    def test_isp_confirmation_waits_for_three_failures_without_spending_pause_chances(self):
        s = self.session()
        s.pause_count = 1
        s.save(update_fields=['pause_count'])
        self.probe.return_value = False
        for _ in range(2):
            self.assertFalse(monitor.check_isp_internet_status(force_probe=True)['isp_outage'])
            s.refresh_from_db()
            self.assertEqual(s.status, 'active')
        self.assertTrue(monitor.check_isp_internet_status(force_probe=True)['isp_outage'])
        s.refresh_from_db()
        self.assertEqual(s.status, 'paused')
        self.assertEqual(s.pause_count, 1)
        self.assertFalse(s.power_paused)

    def test_ordinary_isp_pauses_still_auto_resume_after_restoration(self):
        s = self.session()
        s.pause_session(is_system_pause=True)
        self.mark_outage(s)
        self.clock.return_value += timedelta(minutes=5)
        result = monitor.check_isp_internet_status(force_probe=True)
        s.refresh_from_db()
        self.assertEqual(result['resumed_count'], 1)
        self.assertEqual(s.status, 'active')
        self.assertEqual(s.time_remaining_seconds, 170 * 60)
        self.assertFalse(cache.get(f'outage_paused_{s.pk}'))
        self.allow.assert_called_once()

    def test_stale_isp_ids_cannot_resume_power_holds_or_absent_devices(self):
        s = self.session()
        self.mark_outage(s)
        self.clock.return_value += timedelta(days=3)
        self.boot.return_value = 'boot-b'
        # Use the real monitor: it must recover before processing cached ISP IDs.
        result = monitor.check_isp_internet_status(force_probe=True)
        self.assertEqual(result['resumed_count'], 0)
        with patch('sessions_app.tasks._is_device_reachable', return_value=False):
            auto_resume_connected_sessions()
        s.refresh_from_db()
        self.assertTrue(s.power_paused)
        self.assertEqual(s.status, 'paused')
        self.assertEqual(s.time_remaining_seconds, 170 * 60)
        self.allow.assert_not_called()

    def test_mixed_isp_and_power_pauses_only_resume_the_isp_session(self):
        power = self.session()
        self.clock.return_value += timedelta(days=3)
        self.boot.return_value = 'boot-b'
        ensure_power_recovery()
        ordinary = self.session('AA:BB:CC:DD:EE:02')
        ordinary.pause_session(is_system_pause=True)
        self.mark_outage(power, ordinary)
        result = monitor.check_isp_internet_status(force_probe=True)
        power.refresh_from_db()
        ordinary.refresh_from_db()
        self.assertEqual(result['resumed_count'], 1)
        self.assertTrue(power.power_paused)
        self.assertEqual(power.time_remaining_seconds, 170 * 60)
        self.assertEqual(ordinary.status, 'active')
        self.allow.assert_called_once_with(ordinary.mac_address, rate_kbps=None, upload_kbps=None)

    def test_manual_pause_not_tagged_by_isp_stays_paused(self):
        s = self.session()
        s.pause_session()
        self.mark_outage()
        result = monitor.check_isp_internet_status(force_probe=True)
        s.refresh_from_db()
        self.assertEqual(result['resumed_count'], 0)
        self.assertEqual(s.status, 'paused')
        self.assertFalse(s.power_paused)
        self.allow.assert_not_called()
