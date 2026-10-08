from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings, RequestFactory
from django.utils import timezone

from dashboard.models import SystemSettings
from .models import Plan, Session, SessionPowerState, SuspiciousDevice
from .power_recovery import (ensure_power_recovery, checkpoint_sessions,
                             resume_power_session, PowerRecoveryMiddleware)
from .tasks import (check_expired_sessions, cleanup_expired_and_stale_sessions,
                    auto_resume_connected_sessions, restore_iptables_on_boot,
                    _is_device_reachable)


@override_settings(PISONET_GPIO_SIMULATION=False)
class PowerRecoveryTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 7, 9, 50, tzinfo=dt_timezone.utc)
        self.clock = self.mock('django.utils.timezone.now', return_value=self.now)
        self.boot = self.mock('sessions_app.power_recovery.current_boot_id', return_value='boot-a')
        self.block = self.mock('sessions_app.iptables.block_device', return_value=True)
        self.allow = self.mock('sessions_app.iptables.allow_device', return_value=True)
        self.isp = self.mock('sessions_app.internet_monitor.check_isp_internet_status',
                             return_value={'isp_outage': False, 'is_online': True})
        self.addCleanup(cache.clear)
        self.plan = Plan.objects.create(name='Recovery plan', price=15, duration_minutes=180,
                                        pause_duration_limit=1, pause_limit=1)
        self.settings = SystemSettings.get_settings()
        self.settings.enable_auto_pause_resume = True
        self.settings.auto_pause_timeout_seconds = 120
        self.settings.global_pause_limit_hours = 1
        self.settings.save()
        SessionPowerState.objects.create(pk=1, boot_id='boot-a', checkpoint_at=self.now)
        cache.clear()

    def mock(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def session(self, **overrides):
        values = dict(mac_address='46:D3:34:7C:62:61', plan=self.plan,
                      time_in=self.now - timedelta(minutes=10), amount_paid=15,
                      duration_minutes_purchased=180, status='active', ip_address='10.10.10.178',
                      pause_count=1, initial_bandwidth_mb=0)
        values.update(overrides)
        return Session.objects.create(**values)

    def reboot(self, days=3, seconds=0):
        self.clock.return_value += timedelta(days=days, seconds=seconds)
        self.boot.return_value = 'boot-b'

    def test_active_balance_is_restored_and_frozen_across_days(self):
        s = self.session()
        checkpoint_sessions()
        self.reboot()
        self.assertEqual(ensure_power_recovery(), 1)
        s.refresh_from_db()
        self.assertEqual(s.status, 'paused')
        self.assertTrue(s.power_paused)
        self.assertEqual(s.time_remaining_seconds, 170 * 60)
        self.assertEqual(s.pause_count, 1)  # Exhausted user pauses don't affect power recovery.
        self.clock.return_value += timedelta(days=100)
        self.assertEqual(cleanup_expired_and_stale_sessions(), 0)
        self.assertEqual(s.time_remaining_seconds, 170 * 60)

    def test_expiry_task_running_first_recovers_before_expiring_or_checkpointing(self):
        s = self.session()
        self.reboot()
        check_expired_sessions()
        s.refresh_from_db()
        self.assertTrue(s.power_paused)
        self.assertEqual(s.time_remaining_seconds, 170 * 60)
        self.assertEqual(s.status, 'paused')

    def test_first_dashboard_request_recovers_before_view_expiry(self):
        s = self.session()
        self.reboot()
        middleware = PowerRecoveryMiddleware(lambda request: cleanup_expired_and_stale_sessions())
        self.assertEqual(middleware(RequestFactory().get('/')), 0)
        s.refresh_from_db()
        self.assertTrue(s.power_paused)

    def test_recovery_is_idempotent_through_repeated_boots(self):
        s = self.session()
        self.reboot(days=40)
        ensure_power_recovery()
        s.refresh_from_db()
        first_credit = s.total_paused_seconds
        self.assertEqual(ensure_power_recovery(), 0)
        self.clock.return_value += timedelta(days=40)
        self.boot.return_value = 'boot-c'
        ensure_power_recovery()
        s.refresh_from_db()
        self.assertEqual(s.total_paused_seconds, first_credit)
        self.assertEqual(s.time_remaining_seconds, 170 * 60)

    def test_worker_restart_in_same_boot_does_not_freeze_or_refund(self):
        s = self.session()
        self.clock.return_value += timedelta(seconds=20)
        restore_iptables_on_boot()
        s.refresh_from_db()
        self.assertEqual(s.status, 'active')
        self.assertEqual(s.total_paused_seconds, 0)
        self.assertEqual(s.time_remaining_seconds, 170 * 60 - 20)

    def test_short_outage_is_preserved_too(self):
        s = self.session()
        self.reboot(days=0, seconds=15)
        ensure_power_recovery()
        s.refresh_from_db()
        self.assertEqual(s.time_remaining_seconds, 170 * 60)

    def test_no_phone_keeps_power_session_paused_without_redis_markers(self):
        s = self.session()
        self.reboot()
        ensure_power_recovery()
        cache.clear()
        with patch('sessions_app.tasks._is_device_reachable', return_value=False) as probe:
            auto_resume_connected_sessions()
        s.refresh_from_db()
        self.assertTrue(s.power_paused)
        probe.assert_called_once_with(s.mac_address, s.ip_address, fresh=True)
        self.allow.assert_not_called()

    def test_reconnected_device_resumes_preserved_balance_even_auto_pause_disabled(self):
        s = self.session()
        self.reboot(days=40)
        ensure_power_recovery()
        self.clock.return_value += timedelta(days=10)
        self.settings.enable_auto_pause_resume = False
        self.settings.save()
        cache.clear()
        with patch('sessions_app.tasks._is_device_reachable', return_value=True):
            auto_resume_connected_sessions()
        s.refresh_from_db()
        self.assertEqual(s.status, 'active')
        self.assertFalse(s.power_paused)
        self.assertEqual(s.time_remaining_seconds, 170 * 60)
        self.assertEqual(s.pause_count, 1)
        self.clock.return_value += timedelta(seconds=20)
        self.assertEqual(s.time_remaining_seconds, 170 * 60 - 20)
        # A later ordinary pause must not hit a lifetime ceiling from outage days.
        s.pause_session()
        self.assertEqual(cleanup_expired_and_stale_sessions(), 0)

    def test_blacklisted_device_does_not_auto_resume(self):
        s = self.session()
        self.reboot()
        ensure_power_recovery()
        SuspiciousDevice.objects.create(mac_address=s.mac_address, status='blocked')
        with patch('sessions_app.tasks._is_device_reachable', return_value=True):
            auto_resume_connected_sessions()
        s.refresh_from_db()
        self.assertTrue(s.power_paused)

    def test_manual_pause_is_preserved_but_not_converted_to_auto_resume(self):
        s = self.session()
        s.pause_session()
        remaining = s.time_remaining_seconds
        self.reboot()
        ensure_power_recovery()
        s.refresh_from_db()
        self.assertFalse(s.power_paused)
        self.assertEqual(s.status, 'paused')
        self.assertEqual(s.time_remaining_seconds, remaining)
        with patch('sessions_app.tasks._is_device_reachable', return_value=True):
            auto_resume_connected_sessions()
        s.refresh_from_db()
        self.assertEqual(s.status, 'paused')
        self.clock.return_value += timedelta(hours=2)
        self.assertEqual(cleanup_expired_and_stale_sessions(), 1)

    def test_new_purchase_is_saved_before_next_periodic_checkpoint(self):
        s = self.session()
        checkpoint_sessions()
        self.clock.return_value += timedelta(seconds=5)
        s.extend_session(60)
        s.save()
        remaining = s.power_remaining_seconds
        self.reboot()
        ensure_power_recovery()
        s.refresh_from_db()
        self.assertEqual(s.time_remaining_seconds, remaining)

    def test_ip_update_does_not_destroy_last_good_balance(self):
        s = self.session()
        checkpoint = s.power_checkpoint_at
        remaining = s.power_remaining_seconds
        self.clock.return_value += timedelta(days=3)
        s.ip_address = '10.10.10.100'
        s.save(update_fields=['ip_address'])
        s.refresh_from_db()
        self.assertEqual(s.power_checkpoint_at, checkpoint)
        self.assertEqual(s.power_remaining_seconds, remaining)

    def test_expired_sessions_are_not_resurrected(self):
        s = self.session(status='expired')
        self.reboot()
        ensure_power_recovery()
        s.refresh_from_db()
        self.assertEqual(s.status, 'expired')

    def test_clock_rollback_blocks_views_without_expiring_or_overwriting_state(self):
        s = self.session()
        self.boot.return_value = 'boot-b'
        self.clock.return_value -= timedelta(days=3)
        handler = PowerRecoveryMiddleware(lambda request: self.fail('View must not run'))
        self.assertEqual(handler(RequestFactory().get('/')).status_code, 503)
        s.refresh_from_db()
        self.assertEqual(s.status, 'active')
        self.assertEqual(SessionPowerState.objects.get(pk=1).boot_id, 'boot-a')

    def test_api_status_and_manual_resume_ignore_power_pause_duration_limit(self):
        s = self.session()
        self.reboot()
        ensure_power_recovery()
        self.clock.return_value += timedelta(days=10)
        with patch('sessions_app.views._mac_from_arp', return_value=s.mac_address):
            response = self.client.get('/api/session/status/', {'mac_address': s.mac_address})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'paused')
            response = self.client.post('/api/session/pause/', {'mac_address': s.mac_address})
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()['status'], 'active')
        s.refresh_from_db()
        self.assertEqual(s.time_remaining_seconds, 170 * 60)

    def test_fresh_probe_does_not_trust_passive_arp_or_a_different_phone(self):
        reply = SimpleNamespace(returncode=0, stdout='Unicast reply from 10.10.10.178 [AA:BB:CC:DD:EE:FF]')
        with patch('sessions_app.tasks.subprocess.run', return_value=reply):
            self.assertFalse(_is_device_reachable('46:D3:34:7C:62:61', '10.10.10.178', fresh=True))
        reply.stdout = 'Unicast reply from 10.10.10.178 [46:D3:34:7C:62:61]'
        with patch('sessions_app.tasks.subprocess.run', return_value=reply):
            self.assertTrue(_is_device_reachable('46:D3:34:7C:62:61', '10.10.10.178', fresh=True))

    def test_firewall_failure_keeps_auto_and_customer_resume_paused(self):
        s = self.session()
        self.reboot()
        ensure_power_recovery()
        self.allow.return_value = False
        with patch('sessions_app.tasks._is_device_reachable', return_value=True):
            auto_resume_connected_sessions()
        with patch('sessions_app.views._mac_from_arp', return_value=s.mac_address):
            response = self.client.post('/api/session/pause/', {'mac_address': s.mac_address})
        self.assertEqual(response.status_code, 503)
        s.refresh_from_db()
        self.assertTrue(s.power_paused)
        self.assertEqual(s.time_remaining_seconds, 170 * 60)

    def test_isp_outage_keeps_reconnected_phone_paused(self):
        s = self.session()
        self.reboot()
        ensure_power_recovery()
        self.isp.return_value = {'isp_outage': True}
        with patch('sessions_app.tasks._is_device_reachable', return_value=True):
            auto_resume_connected_sessions()
        s.refresh_from_db()
        self.assertTrue(s.power_paused)
        self.allow.assert_not_called()

    def test_initial_deployment_without_legacy_heartbeat_adopts_current_balance(self):
        s = self.session()
        SessionPowerState.objects.all().delete()
        with patch('sessions_app.power_recovery._legacy_checkpoint', return_value=None):
            self.assertEqual(ensure_power_recovery(), 0)
        s.refresh_from_db()
        self.assertEqual(s.status, 'active')
        self.assertEqual(s.power_remaining_seconds, 170 * 60)

    def test_legacy_heartbeat_bridges_first_upgrade_after_outage(self):
        s = self.session()
        Session.objects.filter(pk=s.pk).update(power_checkpoint_at=None, power_remaining_seconds=None)
        SessionPowerState.objects.all().delete()
        self.reboot()
        with patch('sessions_app.power_recovery._legacy_checkpoint', return_value=self.now):
            ensure_power_recovery()
        s.refresh_from_db()
        self.assertTrue(s.power_paused)
        self.assertEqual(s.time_remaining_seconds, 170 * 60)

    def test_voucher_cleanup_does_not_expire_recovered_paid_time(self):
        from .tasks import expire_voucher_codes
        s = self.session(voucher_code='TST001')
        self.reboot()
        expire_voucher_codes()
        s.refresh_from_db()
        self.assertTrue(s.power_paused)
        self.assertEqual(s.time_remaining_seconds, 170 * 60)

    def test_zero_balance_at_checkpoint_is_not_given_an_infinite_power_hold(self):
        s = self.session(time_in=self.now - timedelta(minutes=181))
        self.reboot()
        ensure_power_recovery()
        s.refresh_from_db()
        self.assertEqual(s.status, 'expired')
        self.assertFalse(s.power_paused)

    def test_portal_page_keeps_power_balance_and_hides_ordinary_pause_expiry_warning(self):
        s = self.session()
        self.reboot()
        response = self.client.get('/session/', {'mac': s.mac_address})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="pause-duration-warning" class="alert alert-warning" style="display: none;')
        s.refresh_from_db()
        self.assertEqual(s.time_remaining_seconds, 170 * 60)

    def test_recovery_failure_rolls_back_all_sessions_and_boot_marker(self):
        s = self.session()
        other = self.session(mac_address='AA:BB:CC:DD:EE:02')
        self.reboot()
        original = Session.save
        def fail_one(instance, *args, **kwargs):
            if instance.pk == other.pk:
                raise RuntimeError('simulated database failure')
            return original(instance, *args, **kwargs)
        with patch.object(Session, 'save', fail_one):
            with self.assertRaises(RuntimeError):
                ensure_power_recovery()
        s.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(s.status, 'active')
        self.assertEqual(other.status, 'active')
        self.assertEqual(SessionPowerState.objects.get(pk=1).boot_id, 'boot-a')
        self.assertEqual(ensure_power_recovery(), 2)

    def test_checkpoint_does_not_overwrite_a_concurrent_extension_snapshot(self):
        s = self.session()
        original = Session._balance_at
        changed = False
        def extend_while_snapshotting(instance, now):
            nonlocal changed
            old_balance = original(instance, now)
            if not changed:
                changed = True
                self.clock.return_value += timedelta(seconds=1)
                newer = Session.objects.get(pk=instance.pk)
                newer.extend_session(60)
                newer.save()
            return old_balance
        with patch.object(Session, '_balance_at', extend_while_snapshotting):
            checkpoint_sessions()
        s.refresh_from_db()
        self.assertEqual(s.power_remaining_seconds, 230 * 60 - 1)

    def test_scheduler_keeps_checkpoint_and_reconnect_tasks(self):
        from pisowifi.celery import app
        schedule = app.conf.beat_schedule
        self.assertEqual(schedule['check-expired-sessions-every-10s']['schedule'], 10.0)
        self.assertIn('auto-resume-connected-sessions-every-30s', schedule)

    def test_firewall_block_happens_before_publishing_recovered_boot(self):
        s = self.session()
        self.reboot()
        def check_order(mac):
            self.assertEqual(SessionPowerState.objects.get(pk=1).boot_id, 'boot-a')
            self.assertTrue(Session.objects.get(pk=s.pk).power_paused)
            return True
        self.block.side_effect = check_order
        ensure_power_recovery()
        self.block.assert_called_once_with(s.mac_address)
        self.assertEqual(SessionPowerState.objects.get(pk=1).boot_id, 'boot-b')

    def test_failed_database_resume_rolls_back_time_and_revokes_access(self):
        s = self.session()
        self.reboot()
        ensure_power_recovery()
        s.refresh_from_db()
        self.block.reset_mock()
        with patch.object(Session, 'resume_session', side_effect=RuntimeError('simulated save failure')):
            self.assertFalse(resume_power_session(s))
        s.refresh_from_db()
        self.assertTrue(s.power_paused)
        self.assertEqual(s.time_remaining_seconds, 170 * 60)
        self.block.assert_called_once_with(s.mac_address)

    def test_duplicate_resume_does_not_restart_timer_or_credit_twice(self):
        s = self.session()
        self.reboot()
        ensure_power_recovery()
        s.refresh_from_db()
        stale = Session.objects.get(pk=s.pk)
        self.assertTrue(resume_power_session(s))
        credit = s.total_paused_seconds
        self.clock.return_value += timedelta(seconds=15)
        self.assertTrue(resume_power_session(stale))
        s.refresh_from_db()
        self.assertEqual(s.total_paused_seconds, credit)
        self.assertEqual(s.time_remaining_seconds, 170 * 60 - 15)
        self.allow.assert_called_once()

    def test_admin_added_credit_stays_paused_if_access_restoration_fails(self):
        from django.contrib.auth import get_user_model
        s = self.session()
        self.reboot()
        ensure_power_recovery()
        admin = get_user_model().objects.create_user('recovery-admin', is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        self.allow.return_value = False
        response = self.client.post(f'/iconnect-ops/sessions/{s.pk}/add_time/', {'minutes': 60}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        s.refresh_from_db()
        self.assertTrue(s.power_paused)
        self.assertEqual(s.time_remaining_seconds, 230 * 60)
