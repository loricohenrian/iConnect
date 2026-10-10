from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from dashboard.models import SystemSettings
from sessions_app.models import DeviceProfile, Plan, Session, SessionPowerState, SpinPrize
from sessions_app.power_recovery import resume_power_session
from sessions_app.tasks import check_expired_sessions
from sessions_app.iptables import allow_device as grant_verified_access


class SpinPrizeAccessTests(TestCase):
    mac = '02:00:00:00:00:01'

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.now = datetime(2026, 10, 10, 2, 0, tzinfo=dt_timezone.utc)
        self.clock = self.mock('django.utils.timezone.now', return_value=self.now)
        self.mock('sessions_app.power_recovery.current_boot_id', return_value='test-boot')
        SessionPowerState.objects.create(pk=1, boot_id='test-boot', checkpoint_at=self.now)
        self.mock('portal.views._get_mac_address', return_value=self.mac)
        self.mock('sessions_app.views._extract_device_name', return_value='Test phone')
        self.mock('sessions_app.bandwidth.get_device_bandwidth_mb', return_value=0)
        self.allow = self.mock('sessions_app.iptables.allow_device', return_value=True)
        self.block = self.mock('sessions_app.iptables.block_device', return_value=True)
        self.baseline = self.mock('sessions_app.iptables.is_forward_default_drop', return_value=True)
        self.isp = self.mock('sessions_app.internet_monitor.check_isp_internet_status',
                             return_value={'isp_outage': False, 'is_online': True})
        self.settings = SystemSettings.get_settings()
        self.settings.enable_spin_wheel = True
        self.settings.spin_cost_points = 20
        self.settings.daily_spin_limit = 3
        self.settings.save()
        self.profile = DeviceProfile.objects.create(mac_address=self.mac, points=100)
        self.prize = SpinPrize.objects.create(
            name='10 Mins Free', minutes_reward=10, probability_weight=1,
            speed_limit=3, speed_limit_upload=2, pause_limit=2,
            pause_duration_limit=24, is_active=True,
        )

    def mock(self, name, **kwargs):
        patcher = patch(name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def spin(self):
        response = self.client.post('/api/execute_spin/', {}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        return response.json()

    def test_standalone_reward_starts_only_after_access_is_granted(self):
        def grant(mac, **kwargs):
            pending = Session.objects.get(mac_address=mac)
            self.assertEqual(pending.status, 'paused')
            self.assertTrue(pending.power_paused)
            self.assertEqual(pending.time_remaining_seconds, 600)
            return True
        self.allow.side_effect = grant
        data = self.spin()
        session = Session.objects.get(pk=data['session_id'])
        self.assertEqual(session.status, 'active')
        self.assertEqual(session.time_remaining_seconds, 600)
        self.assertEqual(session.amount_paid, 0)
        self.assertFalse(data['reward_paused'])
        self.allow.assert_called_once_with(self.mac, rate_kbps=3072, upload_kbps=2048)

    def test_failed_grant_preserves_reward_and_retry_does_not_charge_another_spin(self):
        self.allow.return_value = False
        data = self.spin()
        session = Session.objects.get(pk=data['session_id'])
        self.assertTrue(data['prize']['applied'])  # The win is saved, not discarded.
        self.assertTrue(data['reward_paused'])
        self.assertTrue(session.power_paused)
        self.assertEqual(session.time_remaining_seconds, 600)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points, 80)
        self.assertEqual(self.profile.spins_today, 1)
        self.clock.return_value += timedelta(days=7)
        check_expired_sessions()
        session.refresh_from_db()
        self.assertEqual(session.time_remaining_seconds, 600)
        self.allow.return_value = True
        self.assertTrue(resume_power_session(session))
        self.assertEqual(session.time_remaining_seconds, 600)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points, 80)
        self.assertEqual(self.profile.spins_today, 1)
        self.block.reset_mock()
        self.clock.return_value += timedelta(minutes=10)
        check_expired_sessions()
        session.refresh_from_db()
        self.assertEqual(session.status, 'expired')
        self.block.assert_called_once_with(self.mac)

    def test_grant_exception_keeps_awarded_time_protected(self):
        self.allow.side_effect = RuntimeError('simulated firewall failure')
        data = self.spin()
        session = Session.objects.get(pk=data['session_id'])
        self.assertTrue(data['reward_paused'])
        self.assertEqual(session.time_remaining_seconds, 600)

    def test_real_access_helper_nat_failure_does_not_start_prize_timer(self):
        self.allow.side_effect = grant_verified_access
        with patch('sessions_app.iptables.is_device_allowed', return_value=False), \
                patch('sessions_app.iptables._is_nat_bypass_set', return_value=False), \
                patch('sessions_app.iptables._add_nat_bypass', return_value=False), \
                patch('sessions_app.iptables._run_command', return_value=True):
            data = self.spin()
        session = Session.objects.get(pk=data['session_id'])
        self.assertTrue(data['reward_paused'])
        self.assertEqual(session.time_remaining_seconds, 600)
        self.block.assert_called_once_with(self.mac)

    def test_spending_point_helper_credits_one_point_per_peso_when_enabled(self):
        self.settings.points_per_peso = 1
        self.settings.save()
        DeviceProfile.add_spending_points(self.mac, 1)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points, 101)
        DeviceProfile.add_spending_points(self.mac, 5)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points, 106)

    def test_isp_outage_keeps_reward_paused(self):
        self.isp.return_value = {'isp_outage': True}
        data = self.spin()
        self.assertTrue(data['reward_paused'])
        self.allow.assert_not_called()

    def test_missing_firewall_baseline_keeps_reward_paused(self):
        self.baseline.return_value = False
        self.assertTrue(self.spin()['reward_paused'])
        self.allow.assert_not_called()

    def test_full_network_keeps_reward_paused(self):
        self.settings.max_concurrent_sessions = 1
        self.settings.save()
        Session.objects.create(mac_address='02:00:00:00:00:02', amount_paid=1,
                               duration_minutes_purchased=10, initial_bandwidth_mb=0)
        self.assertTrue(self.spin()['reward_paused'])
        self.allow.assert_not_called()

    def test_existing_paid_session_keeps_its_plan_and_is_extended(self):
        plan = Plan.objects.create(name='Paid', price=10, duration_minutes=120,
                                   speed_limit=6, speed_limit_upload=4)
        session = Session.objects.create(mac_address=self.mac, plan=plan, amount_paid=10,
                                         duration_minutes_purchased=120, initial_bandwidth_mb=0)
        self.spin()
        session.refresh_from_db()
        self.assertEqual(session.duration_minutes_purchased, 130)
        self.assertEqual(session.amount_paid, 10)
        self.assertEqual(session.plan_id, plan.pk)
        self.assertEqual(Session.objects.filter(mac_address=self.mac).count(), 1)
        self.allow.assert_not_called()

    def test_existing_manual_pause_is_not_resumed_by_a_win(self):
        session = Session.objects.create(mac_address=self.mac, amount_paid=1,
                                         duration_minutes_purchased=10, status='paused',
                                         paused_at=self.now, initial_bandwidth_mb=0)
        self.assertTrue(self.spin()['reward_paused'])
        session.refresh_from_db()
        self.assertEqual(session.status, 'paused')
        self.assertEqual(session.duration_minutes_purchased, 20)
        self.allow.assert_not_called()

    def test_zero_minute_prize_does_not_create_or_grant_a_session(self):
        self.prize.minutes_reward = 0
        self.prize.save()
        data = self.spin()
        self.assertIsNone(data['session_id'])
        self.assertFalse(data['prize']['applied'])
        self.assertFalse(Session.objects.exists())
        self.allow.assert_not_called()

    def test_failed_award_rolls_back_points_without_granting_access(self):
        with patch('portal.views.Session.objects.create', side_effect=RuntimeError('simulated save failure')):
            response = self.client.post('/api/execute_spin/', {}, content_type='application/json')
        self.assertEqual(response.status_code, 500)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points, 100)
        self.assertEqual(self.profile.spins_today, 0)
        self.allow.assert_not_called()
