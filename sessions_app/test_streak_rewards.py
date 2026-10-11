"""Daily paid-session rewards, atomic balances and portal read regressions."""
from datetime import datetime, timedelta, timezone as utc
from unittest.mock import patch

from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.db.models.query import QuerySet
from django.http import HttpResponse
from django.test import TestCase, RequestFactory
from rest_framework.test import APIClient

from dashboard.models import SystemSettings
from portal import views as portal_views
from .models import CoinEvent, DeviceProfile, Plan, Session, SpinPrize


class StreakRewardTests(TestCase):
    mac = '02:00:00:00:00:01'
    other_mac = '02:00:00:00:00:02'

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.now = datetime(2026, 10, 11, 2, 0, tzinfo=utc.utc)
        self.clock = self.mock('django.utils.timezone.now', return_value=self.now)
        self.mock('sessions_app.power_recovery.ensure_power_recovery', return_value=0)
        self.mock('sessions_app.internet_monitor.check_isp_internet_status',
                  return_value={'isp_outage': False, 'is_online': True})
        self.mock('sessions_app.iptables.enforce_firewall_baseline', return_value=True)
        self.allow = self.mock('sessions_app.iptables.allow_device', return_value=True)
        self.mock('sessions_app.iptables.block_device', return_value=True)
        self.mock('sessions_app.bandwidth.get_device_bandwidth_mb', return_value=0)
        self.mock('sessions_app.views._mac_from_arp', return_value='')
        self.config = SystemSettings.get_settings()
        self.config.enable_spin_wheel = True
        self.config.points_per_streak_day = 5
        self.config.points_per_peso = 1
        self.config.enable_internet_check = False
        self.config.save()
        self.plan = Plan.objects.create(name='Paid reward test', price=5,
                                        duration_minutes=60, pause_limit=10)

    def mock(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def session(self, **kwargs):
        fields = dict(mac_address=self.mac, plan=self.plan, amount_paid=5,
                      duration_minutes_purchased=60, time_in=self.clock.return_value,
                      ip_address='127.0.0.1', status='active', initial_bandwidth_mb=0)
        fields.update(kwargs)
        return Session.objects.create(**fields)

    def start(self, path='/api/session/start/'):
        CoinEvent.objects.create(mac_address=self.mac, amount=5, denomination=5)
        return self.client.post(path, {'mac_address': self.mac, 'plan_id': self.plan.pk}, format='json')

    def profile(self):
        return DeviceProfile.objects.get(mac_address=self.mac)

    def test_first_paid_start_adds_bonus_and_separate_spending_points(self):
        response = self.start()
        self.assertEqual(response.status_code, 201)
        profile = self.profile()
        self.assertEqual(profile.points, 10)  # 5 spending + 5 daily.
        self.assertEqual(profile.current_streak, 1)
        self.assertEqual(profile.last_connected_date.isoformat(), '2026-10-11')

    def test_multiple_paid_starts_same_day_only_award_one_bonus(self):
        self.assertEqual(self.start().status_code, 201)
        Session.objects.filter(mac_address=self.mac).update(status='expired')
        self.assertEqual(self.start().status_code, 201)
        self.assertEqual(self.profile().points, 15)  # 10 spending + only 5 daily.
        self.assertEqual(self.profile().current_streak, 1)

    def test_following_day_start_awards_five_and_increments_streak(self):
        session = self.session()
        DeviceProfile.record_session_start(session)
        self.clock.return_value += timedelta(days=1)
        DeviceProfile.record_session_start(self.session())
        profile = self.profile()
        self.assertEqual(profile.points, 10)
        self.assertEqual(profile.current_streak, 2)

    def test_missed_day_resets_streak_not_points(self):
        DeviceProfile.record_session_start(self.session())
        self.clock.return_value += timedelta(days=2)
        DeviceProfile.record_session_start(self.session())
        self.assertEqual(self.profile().points, 10)
        self.assertEqual(self.profile().current_streak, 1)

    def test_philippine_midnight_not_utc_midnight_starts_new_day(self):
        self.clock.return_value = datetime(2026, 10, 11, 15, 59, tzinfo=utc.utc)
        session = self.session()
        DeviceProfile.record_session_start(session)
        self.clock.return_value += timedelta(minutes=1)
        DeviceProfile.record_session_start(session)
        self.assertEqual(self.profile().points, 10)
        self.assertEqual(self.profile().last_connected_date.isoformat(), '2026-10-12')

    def test_each_device_has_independent_daily_bonus(self):
        DeviceProfile.record_session_start(self.session())
        other = self.session(mac_address=self.other_mac)
        self.assertEqual(DeviceProfile.record_session_start(other).points, 5)
        self.assertEqual(self.profile().points, 5)

    def test_portal_home_and_spin_pages_do_not_award_bonus(self):
        factory = RequestFactory()
        with patch('portal.views._get_mac_address', return_value=self.mac), patch(
                'portal.views.render', return_value=HttpResponse('ok')):
            for path, view in [('/', portal_views.index), ('/spin/', portal_views.spin_wheel_view),
                               ('/api/spin-data/', portal_views.api_spin_data)]:
                request = factory.get(path)
                request.session = {}
                self.assertEqual(view(request).status_code, 200)
        profile = self.profile()
        self.assertEqual(profile.points, 0)
        self.assertIsNone(profile.last_connected_date)
        self.assertEqual(Session.objects.count(), 0)

    def test_existing_session_page_does_not_award_daily_points(self):
        self.session()
        with patch('portal.views._get_mac_address', return_value=self.mac), patch(
                'portal.views.render', return_value=HttpResponse('ok')):
            request = RequestFactory().get('/session/')
            request.session = {}
            self.assertEqual(portal_views.session_page(request).status_code, 200)
        self.assertEqual(self.profile().points, 0)
        self.assertIsNone(self.profile().last_connected_date)

    def test_insufficient_payment_does_not_award_bonus(self):
        response = self.client.post('/api/session/start/',
            {'mac_address': self.mac, 'plan_id': self.plan.pk}, format='json')
        self.assertEqual(response.status_code, 402)
        self.assertFalse(DeviceProfile.objects.filter(mac_address=self.mac).exists())

    def test_failed_access_rolls_back_start_and_both_point_awards(self):
        self.allow.return_value = False
        self.assertEqual(self.start().status_code, 503)
        self.assertFalse(DeviceProfile.objects.filter(mac_address=self.mac).exists())
        self.assertFalse(Session.objects.filter(mac_address=self.mac).exists())
        self.assertTrue(CoinEvent.objects.filter(mac_address=self.mac, session=None).exists())

    def test_full_network_does_not_award_points(self):
        self.config.max_concurrent_sessions = 1
        self.config.save()
        self.session(mac_address=self.other_mac)
        self.assertEqual(self.start().status_code, 503)
        self.assertFalse(DeviceProfile.objects.filter(mac_address=self.mac).exists())

    def test_paid_voucher_activation_awards_daily_bonus_once(self):
        self.session(status='paused', voucher_code='STREAK1')
        response = self.client.post('/api/session/extend/',
            {'mac_address': self.mac, 'voucher_code': 'STREAK1'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.profile().points, 5)
        self.assertEqual(self.profile().current_streak, 1)

    def test_paid_extend_can_start_session_and_award_daily_bonus(self):
        self.assertEqual(self.start('/api/session/extend-paid/').status_code, 200)
        self.assertEqual(self.profile().points, 10)

    def test_failed_paid_extend_access_does_not_award_daily_bonus(self):
        self.allow.return_value = False
        self.assertEqual(self.start('/api/session/extend-paid/').status_code, 200)
        self.assertEqual(self.profile().points, 5)  # Existing spending rules only.
        self.assertIsNone(self.profile().last_connected_date)

    def test_extension_of_running_session_does_not_count_as_new_daily_start(self):
        self.session()
        self.assertEqual(self.start('/api/session/extend-paid/').status_code, 200)
        self.assertEqual(self.profile().points, 5)
        self.assertIsNone(self.profile().last_connected_date)

    def test_manual_paid_resume_awards_once_not_on_pause(self):
        session = self.session()
        session.pause_session()
        for expected in ['active', 'paused', 'active']:
            response = self.client.post('/api/session/pause/', {'mac_address': self.mac}, format='json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['status'], expected)
        self.assertEqual(self.profile().points, 5)
        self.assertEqual(self.profile().current_streak, 1)

    def test_free_and_not_yet_running_sessions_do_not_earn_bonus(self):
        for fields in [{'amount_paid': 0}, {'status': 'paused'}, {'status': 'expired'},
                       {'duration_minutes_purchased': 0}]:
            with self.subTest(fields=fields):
                self.assertIsNone(DeviceProfile.record_session_start(self.session(**fields)))
        self.assertFalse(DeviceProfile.objects.filter(mac_address=self.mac).exists())

    def test_disabled_wheel_does_not_consume_daily_eligibility(self):
        self.config.enable_spin_wheel = False
        self.config.save()
        session = self.session()
        self.assertIsNone(DeviceProfile.record_session_start(session))
        self.config.enable_spin_wheel = True
        self.config.save()
        self.assertEqual(DeviceProfile.record_session_start(session).points, 5)

    def test_legacy_already_awarded_day_is_not_awarded_twice(self):
        DeviceProfile.objects.create(mac_address=self.mac, points=25, current_streak=3,
                                     last_connected_date=self.now.date())
        DeviceProfile.record_session_start(self.session())
        self.assertEqual(self.profile().points, 25)
        self.assertEqual(self.profile().current_streak, 3)

    def test_zero_bonus_is_not_shown_as_five_in_help(self):
        self.config.points_per_streak_day = 0
        self.config.save()
        self.assertIsNone(DeviceProfile.record_session_start(self.session()))
        with patch('portal.views._get_mac_address', return_value=self.mac):
            response = self.client.get('/session/')
        self.assertContains(response, 'pts once per day')
        self.assertContains(response, 'data.points_per_streak ?? 5')

    def test_rolled_back_activation_also_rolls_back_bonus(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                DeviceProfile.record_session_start(self.session())
                raise RuntimeError('simulate failed activation transaction')
        self.assertFalse(DeviceProfile.objects.filter(mac_address=self.mac).exists())

    def test_overlapping_daily_awards_only_one_update_wins(self):
        session = self.session()
        DeviceProfile.objects.create(mac_address=self.mac)
        update = QuerySet.update
        entered = []
        def interleaved(query, **fields):
            if query.model is DeviceProfile and 'last_connected_date' in fields and not entered:
                entered.append(True)
                DeviceProfile.record_session_start(session)
            return update(query, **fields)
        with patch.object(QuerySet, 'update', autospec=True, side_effect=interleaved):
            DeviceProfile.record_session_start(session)
        self.assertEqual(self.profile().points, 5)
        self.assertEqual(self.profile().current_streak, 1)

    def test_daily_award_preserves_concurrent_spending_and_spin_counters(self):
        session = self.session()
        profile = DeviceProfile.objects.create(mac_address=self.mac)
        update = QuerySet.update
        entered = []
        def interleaved(query, **fields):
            if query.model is DeviceProfile and 'last_connected_date' in fields and not entered:
                entered.append(True)
                update(DeviceProfile.objects.filter(pk=profile.pk), points=F('points')+10, spins_today=1)
            return update(query, **fields)
        with patch.object(QuerySet, 'update', autospec=True, side_effect=interleaved):
            DeviceProfile.record_session_start(session)
        profile.refresh_from_db()
        self.assertEqual(profile.points, 15)
        self.assertEqual(profile.spins_today, 1)

    def test_spending_award_preserves_concurrent_spin_deduction(self):
        profile = DeviceProfile.objects.create(mac_address=self.mac, points=20)
        update = QuerySet.update
        entered = []
        def interleaved(query, **fields):
            if query.model is DeviceProfile and 'points' in fields and not entered:
                entered.append(True)
                update(DeviceProfile.objects.filter(pk=profile.pk), points=F('points')-10, spins_today=1)
            return update(query, **fields)
        with patch.object(QuerySet, 'update', autospec=True, side_effect=interleaved):
            DeviceProfile.add_spending_points(self.mac, 5)
        profile.refresh_from_db()
        self.assertEqual(profile.points, 15)
        self.assertEqual(profile.spins_today, 1)

    def test_two_overlapping_spending_awards_both_count(self):
        DeviceProfile.objects.create(mac_address=self.mac)
        update = QuerySet.update
        entered = []
        def interleaved(query, **fields):
            if query.model is DeviceProfile and not entered:
                entered.append(True)
                DeviceProfile.add_spending_points(self.mac, 10)
            return update(query, **fields)
        with patch.object(QuerySet, 'update', autospec=True, side_effect=interleaved):
            DeviceProfile.add_spending_points(self.mac, 5)
        self.assertEqual(self.profile().points, 15)

    def test_paid_group_host_and_joiner_each_get_one_daily_bonus(self):
        CoinEvent.objects.create(mac_address=self.mac, amount=10, denomination=10)
        started = self.client.post('/api/session/start/',
            {'mac_address': self.mac, 'plan_id': self.plan.pk,
             'is_group_pass': True, 'group_pass_devices': 2}, format='json')
        self.assertEqual(started.status_code, 201)
        self.assertEqual(self.profile().points, 10)
        joined = self.client.post('/api/session/join-group/',
            {'mac_address': self.other_mac, 'group_code': started.data['session_group']}, format='json')
        self.assertEqual(joined.status_code, 201)
        other = DeviceProfile.objects.get(mac_address=self.other_mac)
        self.assertEqual(other.points, 10)
        self.assertEqual(other.current_streak, 1)
        repeated = self.client.post('/api/session/join-group/',
            {'mac_address': self.other_mac, 'group_code': started.data['session_group']}, format='json')
        self.assertEqual(repeated.status_code, 409)
        other.refresh_from_db()
        self.assertEqual(other.points, 10)

    def test_failed_voucher_access_does_not_award_bonus(self):
        voucher = self.session(status='paused', voucher_code='STREAK2')
        self.allow.return_value = False
        response = self.client.post('/api/session/extend/',
            {'mac_address': self.mac, 'voucher_code': 'STREAK2'}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(DeviceProfile.objects.filter(mac_address=self.mac).exists())
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'paused')

    def test_free_spin_does_not_create_daily_bonus(self):
        DeviceProfile.objects.create(mac_address=self.mac, points=20)
        self.config.spin_cost_points = 10
        self.config.save()
        SpinPrize.objects.create(name='Try again', minutes_reward=0, probability_weight=1)
        with patch('portal.views._get_mac_address', return_value=self.mac):
            response = self.client.post('/api/execute_spin/', {}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        self.assertEqual(self.profile().points, 10)
        self.assertEqual(self.profile().spins_today, 1)
        self.assertIsNone(self.profile().last_connected_date)

    def test_failed_manual_resume_does_not_award_bonus(self):
        self.session(status='paused', paused_at=self.now)
        self.allow.return_value = False
        response = self.client.post('/api/session/pause/', {'mac_address': self.mac}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DeviceProfile.objects.filter(mac_address=self.mac).exists())

    def test_clock_rollback_does_not_reaward_a_recorded_day(self):
        DeviceProfile.objects.create(mac_address=self.mac, points=5, current_streak=1,
                                     last_connected_date=(self.now+timedelta(days=1)).date())
        DeviceProfile.record_session_start(self.session())
        self.assertEqual(self.profile().points, 5)

    def test_bonus_does_not_depend_on_spending_points_being_enabled(self):
        self.config.points_per_peso = 0
        self.config.save()
        self.assertEqual(self.start().status_code, 201)
        self.assertEqual(self.profile().points, 5)

    def test_configured_bonus_is_used_instead_of_hard_coded_five(self):
        self.config.points_per_streak_day = 7
        self.config.save()
        DeviceProfile.record_session_start(self.session())
        self.assertEqual(self.profile().points, 7)
