"""Disconnect detection, bounded scans, and paid-time preservation regressions."""
from datetime import datetime, timedelta, timezone as dt_timezone
from threading import Barrier
from types import SimpleNamespace
import subprocess
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from dashboard.models import SystemSettings
from .models import Plan, Session
from .tasks import (_disconnect_cache_key, _is_device_reachable,
                    _probe_session_devices, _single_flight_scan,
                    auto_pause_disconnected_sessions, auto_resume_connected_sessions,
                    expire_voucher_codes)


class DeviceReachabilityTests(SimpleTestCase):
    mac = 'CA:EB:CB:DB:E4:70'
    ip = '10.10.10.168'

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_cached_arp_entry_is_not_proof_of_connection(self):
        with patch('builtins.open') as arp_table, patch(
                'sessions_app.tasks.subprocess.run',
                return_value=SimpleNamespace(returncode=1, stdout=b'')) as probe:
            self.assertFalse(_is_device_reachable(self.mac, self.ip))
        arp_table.assert_not_called()
        probe.assert_called_once_with(['arping', '-c', '1', '-w', '1', self.ip],
                                      capture_output=True, timeout=2)

    def test_reply_from_different_mac_does_not_count(self):
        reply = SimpleNamespace(returncode=0,
                                stdout=f'Unicast reply from {self.ip} [AA:BB:CC:DD:EE:FF]')
        with patch('sessions_app.tasks.subprocess.run', return_value=reply):
            self.assertFalse(_is_device_reachable(self.mac, self.ip))

    def test_matching_fresh_reply_counts_even_when_icmp_is_blocked(self):
        reply = SimpleNamespace(returncode=0,
                                stdout=f'Unicast reply from {self.ip} [{self.mac.lower()}]'.encode())
        with patch('sessions_app.tasks.subprocess.run', return_value=reply) as probe:
            self.assertTrue(_is_device_reachable(self.mac, self.ip))
        self.assertEqual(probe.call_count, 1)  # No ICMP fallback.

    def test_probe_failures_are_unknown_not_offline(self):
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired('arping', 2)):
            with self.subTest(failure=type(failure).__name__), patch(
                    'sessions_app.tasks.subprocess.run', side_effect=failure):
                self.assertIsNone(_is_device_reachable(self.mac, self.ip))
        with patch('sessions_app.tasks.subprocess.run',
                   return_value=SimpleNamespace(returncode=2, stdout=b'')):
            self.assertIsNone(_is_device_reachable(self.mac, self.ip))

    def test_probes_run_in_parallel_without_database_access(self):
        sessions = [SimpleNamespace(pk=i, mac_address=self.mac, ip_address=f'10.10.10.{20+i}')
                    for i in range(4)]
        simultaneous = Barrier(4, timeout=3)

        def probe(*args, **kwargs):
            simultaneous.wait()
            return False

        with patch('sessions_app.tasks._is_device_reachable', side_effect=probe):
            self.assertEqual(_probe_session_devices(sessions), {i: False for i in range(4)})

    def test_duplicate_device_is_probed_only_once(self):
        sessions = [SimpleNamespace(pk=i, mac_address=self.mac, ip_address=self.ip) for i in (1, 2)]
        with patch('sessions_app.tasks._is_device_reachable', return_value=True) as probe:
            self.assertEqual(_probe_session_devices(sessions), {1: True, 2: True})
        probe.assert_called_once_with(self.mac, self.ip, fresh=True)

    def test_overlapping_scan_is_skipped(self):
        cache.set('wifi_scan_lock:test', 'other-worker', timeout=120)
        calls = []
        job = _single_flight_scan('test')(lambda: calls.append(True))
        self.assertIn('scan already running', job())
        self.assertEqual(calls, [])
        self.assertEqual(cache.get('wifi_scan_lock:test'), 'other-worker')

    def test_scan_lock_is_released_after_failure(self):
        def fail():
            raise ValueError('test failure')
        with self.assertRaises(ValueError):
            _single_flight_scan('test')(fail)()
        self.assertIsNone(cache.get('wifi_scan_lock:test'))

    def test_expired_lock_owner_cannot_delete_new_owners_lock(self):
        def replaced():
            cache.set('wifi_scan_lock:test', 'new-worker', timeout=120)
        _single_flight_scan('test')(replaced)()
        self.assertEqual(cache.get('wifi_scan_lock:test'), 'new-worker')

    def test_disconnect_checks_are_scheduled_every_ten_seconds(self):
        entries = [item for item in settings.CELERY_BEAT_SCHEDULE.values()
                   if item['task'] == 'sessions_app.tasks.auto_pause_disconnected_sessions']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['schedule'], 10.0)
        self.assertEqual(entries[0]['options']['expires'], 20)


@override_settings(PISONET_GPIO_SIMULATION=False)
class WifiDisconnectTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.now = datetime(2026, 10, 11, 8, 0, tzinfo=dt_timezone.utc)
        self.clock = self.mock('django.utils.timezone.now', return_value=self.now)
        self.mock('sessions_app.power_recovery.ensure_power_recovery', return_value=0)
        self.probe = self.mock('sessions_app.tasks._is_device_reachable', return_value=False)
        self.block = self.mock('sessions_app.iptables.block_device', return_value=True)
        self.allow = self.mock('sessions_app.iptables.allow_device', return_value=True)
        self.isp = self.mock('sessions_app.internet_monitor.check_isp_internet_status',
                             return_value={'isp_outage': False})
        self.config = SystemSettings.get_settings()
        self.config.enable_auto_pause_resume = True
        self.config.auto_pause_timeout_seconds = 60
        self.config.save()
        self.plan = Plan.objects.create(name='Disconnect test', price=5, duration_minutes=60,
                                        pause_limit=6, pause_duration_limit=24)
        self.session = Session.objects.create(mac_address='CA:EB:CB:DB:E4:70',
            ip_address='10.10.10.168', plan=self.plan, amount_paid=5,
            duration_minutes_purchased=60, remaining_minutes=60,
            time_in=self.now, status='active', initial_bandwidth_mb=0)

    def mock(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def mark_offline(self):
        cache.set(_disconnect_cache_key(self.session),
                  (self.clock.return_value - timedelta(seconds=60)).isoformat(), timeout=180)

    def test_sixty_second_timeout_pauses_once_and_freezes_balance(self):
        auto_pause_disconnected_sessions()
        self.clock.return_value += timedelta(seconds=59)
        auto_pause_disconnected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'active')
        self.block.assert_not_called()
        self.clock.return_value += timedelta(seconds=1)
        auto_pause_disconnected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.assertEqual(self.session.pause_count, 1)
        self.assertEqual(self.session.time_remaining_seconds, 3540)
        self.assertTrue(cache.get(f'auto_paused_{self.session.pk}'))
        self.clock.return_value += timedelta(minutes=10)
        auto_pause_disconnected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.time_remaining_seconds, 3540)
        self.assertEqual(self.session.pause_count, 1)
        self.block.assert_called_once_with(self.session.mac_address)

    def test_used_auto_paused_voucher_is_not_expired_as_unused(self):
        self.session.voucher_code = 'USED001'
        self.session.save()
        self.mark_offline()
        auto_pause_disconnected_sessions()
        unused = Session.objects.create(mac_address='AA:BB:CC:DD:EE:FF',
            plan=self.plan, duration_minutes_purchased=60, amount_paid=5,
            status='paused', voucher_code='NEW0001', initial_bandwidth_mb=0)
        Session.objects.filter(pk__in=[self.session.pk, unused.pk]).update(
            created_at=self.now-timedelta(minutes=10))
        expire_voucher_codes()
        self.session.refresh_from_db()
        unused.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.assertEqual(unused.status, 'expired')

    def test_brief_disconnect_does_not_spend_a_pause(self):
        auto_pause_disconnected_sessions()
        self.clock.return_value += timedelta(seconds=40)
        self.probe.return_value = True
        auto_pause_disconnected_sessions()
        self.assertIsNone(cache.get(_disconnect_cache_key(self.session)))
        self.probe.return_value = False
        self.clock.return_value += timedelta(seconds=30)
        auto_pause_disconnected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'active')
        self.assertEqual(self.session.pause_count, 0)

    def test_old_mac_timer_does_not_pause_a_new_session(self):
        cache.set('auto_pause_unreachable_'+self.session.mac_address,
                  (self.now-timedelta(hours=1)).isoformat(), timeout=180)
        auto_pause_disconnected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'active')

    def test_missing_probe_does_not_pause_or_resume_customer(self):
        self.mark_offline()
        self.probe.return_value = None
        auto_pause_disconnected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'active')
        self.block.assert_not_called()

    def test_reconnect_requires_fresh_reply_and_restores_access_before_timer(self):
        self.clock.return_value += timedelta(seconds=60)
        self.mark_offline()
        auto_pause_disconnected_sessions()
        self.clock.return_value += timedelta(minutes=5)
        auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.allow.assert_not_called()
        self.probe.return_value = True

        def allow(*args, **kwargs):
            self.assertEqual(Session.objects.get(pk=self.session.pk).status, 'paused')
            return True
        self.allow.side_effect = allow
        auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'active')
        self.assertEqual(self.session.time_remaining_seconds, 3540)
        self.assertEqual(self.session.pause_count, 1)
        self.assertIsNone(cache.get(f'auto_paused_{self.session.pk}'))
        self.probe.assert_called_with(self.session.mac_address, self.session.ip_address, fresh=True)

    def test_firewall_failure_keeps_disconnected_session_paused(self):
        self.mark_offline()
        auto_pause_disconnected_sessions()
        self.probe.return_value = True
        self.allow.return_value = False
        auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.assertTrue(cache.get(f'auto_paused_{self.session.pk}'))

    def test_failed_revocation_does_not_freeze_timer_with_access_open(self):
        self.mark_offline()
        self.block.return_value = False
        auto_pause_disconnected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'active')
        self.assertEqual(self.session.pause_count, 0)
        self.assertIsNone(cache.get(f'auto_paused_{self.session.pk}'))
        self.assertIsNotNone(cache.get(_disconnect_cache_key(self.session)))
        self.block.return_value = True
        auto_pause_disconnected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.assertEqual(self.session.pause_count, 1)

    def test_failed_resume_save_revokes_access_and_preserves_time(self):
        self.mark_offline()
        auto_pause_disconnected_sessions()
        remaining = Session.objects.get(pk=self.session.pk).time_remaining_seconds
        self.probe.return_value = True
        with patch('sessions_app.models.Session.resume_session', side_effect=RuntimeError('test save failure')):
            auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.assertEqual(self.session.time_remaining_seconds, remaining)
        self.assertTrue(cache.get(f'auto_paused_{self.session.pk}'))
        self.assertEqual(self.block.call_count, 2)

    def test_blacklisted_auto_paused_device_does_not_resume(self):
        from .models import SuspiciousDevice
        self.mark_offline()
        auto_pause_disconnected_sessions()
        SuspiciousDevice.objects.create(mac_address=self.session.mac_address, is_blocked=True)
        self.probe.return_value = True
        auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.allow.assert_not_called()

    def test_manual_pause_is_never_auto_resumed(self):
        self.session.pause_session()
        cache.set(f'manual_pause_{self.session.pk}', True)
        self.probe.return_value = True
        auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.probe.assert_not_called()

    def test_changed_session_during_probe_is_not_auto_paused(self):
        self.mark_offline()
        def changed(sessions):
            Session.objects.filter(pk=self.session.pk).update(ip_address='10.10.10.169')
            return {self.session.pk: False}
        with patch('sessions_app.tasks._probe_session_devices', side_effect=changed):
            auto_pause_disconnected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'active')
        self.block.assert_not_called()

    def test_exhausted_pause_limit_is_preserved(self):
        self.session.pause_count = 6
        self.session.save()
        self.mark_offline()
        auto_pause_disconnected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'active')
        self.assertEqual(self.session.pause_count, 6)

    def test_isp_outage_does_not_resume_disconnected_sessions(self):
        self.mark_offline()
        auto_pause_disconnected_sessions()
        self.probe.return_value = True
        self.isp.return_value = {'isp_outage': True}
        auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.allow.assert_not_called()

    def auto_pause_for_reconnect(self):
        self.mark_offline()
        auto_pause_disconnected_sessions()
        self.probe.return_value = True

    def another_session(self, status='active'):
        return Session.objects.create(mac_address='AA:BB:CC:DD:EE:01',
            ip_address='10.10.10.170', plan=self.plan, amount_paid=5,
            duration_minutes_purchased=60, remaining_minutes=60,
            time_in=self.now, status=status, initial_bandwidth_mb=0)

    def test_full_network_keeps_auto_paused_balance(self):
        self.auto_pause_for_reconnect()
        self.config.max_concurrent_sessions = 1
        self.config.save()
        self.another_session()
        remaining = Session.objects.get(pk=self.session.pk).time_remaining_seconds
        auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.assertEqual(self.session.time_remaining_seconds, remaining)
        self.assertTrue(cache.get(f'auto_paused_{self.session.pk}'))
        self.allow.assert_not_called()

    def test_only_available_slot_is_used_when_two_devices_reconnect(self):
        self.auto_pause_for_reconnect()
        self.config.max_concurrent_sessions = 1
        self.config.save()
        other = self.another_session()
        other.pause_session()
        cache.set(f'auto_paused_{other.pk}', True)
        auto_resume_connected_sessions()
        self.assertEqual(Session.objects.filter(status='active').count(), 1)
        self.assertEqual(Session.objects.filter(status='paused').count(), 1)
        self.assertEqual(self.allow.call_count, 1)

    def test_capacity_is_checked_after_probe_not_before(self):
        self.auto_pause_for_reconnect()
        self.config.max_concurrent_sessions = 1
        self.config.save()
        def changed(sessions):
            self.another_session()
            return {s.pk: True for s in sessions}
        with patch('sessions_app.tasks._probe_session_devices', side_effect=changed):
            auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.allow.assert_not_called()

    def test_failed_grant_leaves_slot_for_next_reconnecting_device(self):
        self.auto_pause_for_reconnect()
        self.config.max_concurrent_sessions = 1
        self.config.save()
        other = self.another_session()
        other.pause_session()
        cache.set(f'auto_paused_{other.pk}', True)
        self.allow.side_effect = [False, True]
        auto_resume_connected_sessions()
        self.assertEqual(Session.objects.filter(status='active').count(), 1)
        self.assertEqual(Session.objects.filter(status='paused').count(), 1)
        self.assertEqual(self.allow.call_count, 2)

    def test_changed_identity_during_probe_does_not_resume(self):
        for change in ({'ip_address': '10.10.10.169'},
                       {'mac_address': 'AA:BB:CC:DD:EE:22'}):
            with self.subTest(change=change):
                self.auto_pause_for_reconnect()
                def changed(sessions):
                    Session.objects.filter(pk=self.session.pk).update(**change)
                    return {s.pk: True for s in sessions}
                with patch('sessions_app.tasks._probe_session_devices', side_effect=changed):
                    auto_resume_connected_sessions()
                self.session.refresh_from_db()
                self.assertEqual(self.session.status, 'paused')
                self.allow.assert_not_called()

    def test_disabled_during_probe_does_not_resume(self):
        self.auto_pause_for_reconnect()
        def changed(sessions):
            SystemSettings.objects.filter(pk=1).update(enable_auto_pause_resume=False)
            return {s.pk: True for s in sessions}
        with patch('sessions_app.tasks._probe_session_devices', side_effect=changed):
            auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.allow.assert_not_called()

    def test_manual_pause_marker_added_during_probe_does_not_resume(self):
        self.auto_pause_for_reconnect()
        def changed(sessions):
            cache.set(f'manual_pause_{self.session.pk}', True)
            return {s.pk: True for s in sessions}
        with patch('sessions_app.tasks._probe_session_devices', side_effect=changed):
            auto_resume_connected_sessions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'paused')
        self.allow.assert_not_called()

    def test_full_network_does_not_resume_power_held_device(self):
        self.session.pause_session(is_system_pause=True)
        self.session.power_paused = True
        self.session.save()
        self.config.max_concurrent_sessions = 1
        self.config.save()
        self.another_session()
        self.probe.return_value = True
        with patch('sessions_app.power_recovery.resume_power_session') as resume:
            auto_resume_connected_sessions()
        resume.assert_not_called()
        self.session.refresh_from_db()
        self.assertTrue(self.session.power_paused)
