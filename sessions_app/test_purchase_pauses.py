"""Paid pause allowances scale with purchased units, not payment batching."""
from datetime import datetime, timedelta, timezone as utc
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from dashboard.models import SystemSettings
from .models import CoinEvent, Plan, Session
from .views import _purchase_pause_allowance, calculate_combo_for_amount


class PurchasePauseTests(TestCase):
    mac = '02:00:00:00:01:01'
    other_mac = '02:00:00:00:01:02'

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.now = datetime(2026, 10, 12, 1, 0, tzinfo=utc.utc)
        self.clock = self.mock('django.utils.timezone.now', return_value=self.now)
        self.mock('sessions_app.power_recovery.ensure_power_recovery', return_value=0)
        self.mock('sessions_app.internet_monitor.check_isp_internet_status',
                  return_value={'isp_outage': False, 'is_online': True})
        self.mock('sessions_app.iptables.enforce_firewall_baseline', return_value=True)
        self.allow = self.mock('sessions_app.iptables.allow_device', return_value=True)
        self.mock('sessions_app.iptables.block_device', return_value=True)
        self.mock('sessions_app.iptables.apply_bandwidth_limit', return_value=True)
        self.mock('sessions_app.bandwidth.get_device_bandwidth_mb', return_value=0)
        self.mock('sessions_app.views._mac_from_arp', return_value='')
        config = SystemSettings.get_settings()
        config.enable_internet_check = False
        config.enable_spin_wheel = False
        config.max_session_pause_cap = 5
        config.min_extend_amount_for_pause = 5
        config.save()
        self.plan = Plan.objects.create(name='P20', price=20, duration_minutes=300,
                                       speed_limit=8, pause_limit=15, pause_duration_limit=168)
        self.small = Plan.objects.create(name='P1', price=1, duration_minutes=10,
                                        speed_limit=3, pause_limit=3, pause_duration_limit=24)

    def mock(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def purchase(self, amount, *, mac=None, extend=False, auto=False, plan=None, **extra):
        mac = mac or self.mac
        CoinEvent.objects.create(mac_address=mac, amount=amount,
                                 denomination=20 if amount >= 20 else 1)
        body = {'mac_address': mac, **extra}
        if not auto:
            body['plan_id'] = (plan or self.plan).pk
        path = '/api/session/extend-paid/' if extend else '/api/session/start/'
        response = self.client.post(path, body, format='json')
        self.assertEqual(response.status_code, 200 if extend else 201, response.data)
        return Session.objects.get(pk=response.data['session']['id'])

    def session(self, **extra):
        fields = dict(mac_address=self.mac, plan=self.plan, amount_paid=20,
                      duration_minutes_purchased=300, time_in=self.now,
                      ip_address='127.0.0.1', status='active', initial_bandwidth_mb=0)
        fields.update(extra)
        return Session.objects.create(**fields)

    def test_100_pesos_start_has_75_pauses_and_fixed_168_hours(self):
        session = self.purchase(100)
        self.assertEqual(session.amount_paid, 100)
        self.assertEqual(session.duration_minutes_purchased, 1500)
        self.assertEqual(session.effective_pause_limit, 75)
        self.assertEqual(session.pauses_left, 75)
        self.assertEqual(session.plan.pause_duration_limit, 168)
        self.assertEqual(Session.objects.count(), 1)

    def test_five_20_peso_purchases_match_one_100_peso_purchase(self):
        batch = self.purchase(100)
        split = self.purchase(20, mac=self.other_mac)
        for _ in range(4):
            split = self.purchase(20, mac=self.other_mac, extend=True)
        for field in ['amount_paid', 'duration_minutes_purchased', 'pause_limit', 'pause_count']:
            self.assertEqual(getattr(split, field), getattr(batch, field), field)
        self.assertEqual(split.pauses_left, 75)
        self.assertEqual(split.plan.pause_duration_limit, 168)

    def test_new_session_via_extend_endpoint_does_not_double_first_allowance(self):
        session = self.purchase(100, extend=True)
        self.assertEqual(session.pause_limit, 75)
        self.assertEqual(session.duration_minutes_purchased, 1500)

    def test_paid_extension_keeps_used_pauses_used(self):
        original = self.session(pause_count=4)
        session = self.purchase(100, extend=True)
        self.assertEqual(session.pk, original.pk)
        self.assertEqual(session.pause_count, 4)
        self.assertEqual(session.pause_limit, 90)
        self.assertEqual(session.pauses_left, 86)

    def test_exhausted_allowance_gets_only_new_purchased_pauses(self):
        self.session(pause_count=15)
        session = self.purchase(20, extend=True)
        self.assertEqual(session.pause_count, 15)
        self.assertEqual(session.pause_limit, 30)
        self.assertEqual(session.pauses_left, 15)

    def test_expired_session_starts_with_only_new_purchase_allowance(self):
        self.session(status='expired', pause_count=15, pause_limit=75)
        session = self.purchase(100, extend=True)
        self.assertEqual(session.pause_limit, 75)
        self.assertEqual(session.pause_count, 0)
        self.assertEqual(session.duration_minutes_purchased, 1500)

    def test_paused_session_purchase_preserves_used_count(self):
        self.session(status='paused', paused_at=self.now, pause_count=2)
        session = self.purchase(20, extend=True)
        self.assertEqual(session.pause_count, 2)
        self.assertEqual(session.pause_limit, 30)
        self.assertEqual(session.pauses_left, 28)

    def test_auto_combo_100_pesos_start_and_split_payments_match(self):
        batch = self.purchase(100, auto=True)
        split = self.purchase(20, mac=self.other_mac, auto=True)
        for _ in range(4):
            split = self.purchase(20, mac=self.other_mac, auto=True, extend=True)
        self.assertEqual(batch.pause_limit, 75)
        self.assertEqual(split.pause_limit, 75)
        self.assertEqual(batch.duration_minutes_purchased, split.duration_minutes_purchased)

    def test_mixed_combo_counts_each_component_not_just_highest_plan(self):
        session = self.purchase(42, auto=True)
        self.assertEqual(session.pause_limit, 2 * 15 + 2 * 3)
        self.assertEqual(session.duration_minutes_purchased, 2 * 300 + 2 * 10)
        self.assertEqual(session.plan_id, self.plan.pk)
        self.assertEqual(session.plan.pause_duration_limit, 168)

    def test_mixed_combo_extension_matches_separate_component_purchases(self):
        self.purchase(20)
        self.purchase(20, mac=self.other_mac)
        batch = self.purchase(42, auto=True, extend=True)
        self.purchase(20, mac=self.other_mac, extend=True)
        self.purchase(20, mac=self.other_mac, extend=True)
        split = self.purchase(2, mac=self.other_mac, extend=True, plan=self.small)
        self.assertEqual(batch.pause_limit, split.pause_limit)
        self.assertEqual(batch.pause_limit, 51)
        self.assertEqual(batch.duration_minutes_purchased, split.duration_minutes_purchased)

    def test_one_peso_paid_unit_is_not_excluded_by_legacy_threshold(self):
        self.session()
        session = self.purchase(1, plan=self.small, extend=True)
        self.assertEqual(session.pause_limit, 18)

    def test_speed_upgrade_keeps_original_inherited_allowance(self):
        self.session(plan=self.small, amount_paid=1, duration_minutes_purchased=10, pause_count=1)
        session = self.purchase(20, extend=True)
        self.assertEqual(session.plan_id, self.plan.pk)
        self.assertEqual(session.pause_limit, 18)
        self.assertEqual(session.pauses_left, 17)

    def test_unlimited_purchase_and_existing_unlimited_remain_unlimited(self):
        self.small.pause_limit = 0
        self.small.save(update_fields=['pause_limit'])
        self.session()
        session = self.purchase(1, plan=self.small, extend=True)
        self.assertEqual(session.pauses_left, 'Unlimited')
        session = self.purchase(20, extend=True)
        self.assertEqual(session.pauses_left, 'Unlimited')

    def test_mixed_combo_with_unlimited_component_is_unlimited(self):
        self.small.pause_limit = 0
        self.small.save(update_fields=['pause_limit'])
        session = self.purchase(21, auto=True)
        self.assertEqual(session.pause_limit, 0)
        self.assertEqual(session.pauses_left, 'Unlimited')

    def test_unspent_remainder_does_not_grant_unpurchased_pauses(self):
        self.small.is_active = False
        self.small.save(update_fields=['is_active'])
        session = self.purchase(105, auto=True)
        self.assertEqual(session.amount_paid, 100)
        self.assertEqual(session.pause_limit, 75)
        self.assertEqual(CoinEvent.objects.filter(session__isnull=True).get().amount, 5)

    def test_used_pause_deducts_exactly_one_from_multiplied_allowance(self):
        session = self.purchase(100)
        response = self.client.post('/api/session/pause/', {'mac_address': self.mac}, format='json')
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.pause_count, 1)
        self.assertEqual(session.pauses_left, 74)
        response = self.client.get('/api/session/status/', {'mac_address': self.mac})
        self.assertEqual(response.data['pauses_left'], 74)

    def test_168_hour_pause_expiry_does_not_multiply_with_100_pesos(self):
        session = self.purchase(100)
        session.pause_session()
        self.clock.return_value += timedelta(hours=167)
        self.assertGreater(session.time_remaining_seconds, 0)
        self.clock.return_value += timedelta(hours=1)
        self.assertEqual(session.time_remaining_seconds, 0)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.pause_duration_limit, 168)

    def test_bonus_cap_never_removes_existing_purchased_allowances(self):
        session = self.purchase(100)
        session.add_pauses(3, cap=5)
        self.assertEqual(session.pause_limit, 75)

    def test_free_bonus_still_uses_existing_cap(self):
        session = self.session(plan=self.small, pause_limit=3)
        session.add_pauses(9, cap=5)
        self.assertEqual(session.pause_limit, 5)

    def test_group_host_and_joiner_get_one_allowance_per_device(self):
        host = self.purchase(40, is_group_pass=True, group_pass_devices=2)
        self.assertEqual(host.pause_limit, 15)
        self.assertEqual(host.duration_minutes_purchased, 300)
        response = self.client.post('/api/session/join-group/',
            {'mac_address': self.other_mac, 'group_code': host.session_group.group_code}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        joined = Session.objects.get(mac_address=self.other_mac)
        self.assertEqual(joined.pause_limit, 15)

    def test_group_redeemed_into_existing_session_adds_one_plan_allowance(self):
        host = self.purchase(40, is_group_pass=True, group_pass_devices=2)
        existing = self.session(mac_address=self.other_mac, pause_count=3)
        response = self.client.post('/api/session/join-group/',
            {'mac_address': self.other_mac, 'group_code': host.session_group.group_code}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        existing.refresh_from_db()
        self.assertEqual(existing.pause_limit, 30)
        self.assertEqual(existing.pauses_left, 27)

    def test_voucher_transfer_retains_its_explicit_scaled_allowance(self):
        target = self.session(pause_count=2)
        voucher = self.session(status='paused', voucher_code='PAUSE75', amount_paid=100,
                               duration_minutes_purchased=1500, pause_limit=75)
        response = self.client.post('/api/session/extend/',
            {'mac_address': self.mac, 'voucher_code': voucher.voucher_code}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        target.refresh_from_db()
        self.assertEqual(target.pause_limit, 90)
        self.assertEqual(target.pauses_left, 88)
        self.assertEqual(target.plan.pause_duration_limit, 168)

    def test_single_use_voucher_cannot_grant_allowances_twice(self):
        target = self.session()
        voucher = self.session(status='paused', voucher_code='PAUSE15')
        body = {'mac_address': self.mac, 'voucher_code': voucher.voucher_code}
        self.assertEqual(self.client.post('/api/session/extend/', body, format='json').status_code, 200)
        self.assertEqual(self.client.post('/api/session/extend/', body, format='json').status_code, 404)
        target.refresh_from_db()
        self.assertEqual(target.pause_limit, 30)

    def test_planless_voucher_without_allowance_adds_only_time(self):
        target = self.session(pause_count=2)
        voucher = self.session(status='paused', voucher_code='TIMEONLY', plan=None,
                               pause_limit=None, amount_paid=0, duration_minutes_purchased=30)
        response = self.client.post('/api/session/extend/',
            {'mac_address': self.mac, 'voucher_code': voucher.voucher_code}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        target.refresh_from_db()
        self.assertEqual(target.pauses_left, 13)
        self.assertEqual(target.duration_minutes_purchased, 330)

    def test_failed_paid_start_rolls_back_pause_allowance_and_coin_consumption(self):
        CoinEvent.objects.create(mac_address=self.mac, amount=100, denomination=20)
        self.allow.return_value = False
        response = self.client.post('/api/session/start/',
            {'mac_address': self.mac, 'plan_id': self.plan.pk}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(Session.objects.exists())
        self.assertTrue(CoinEvent.objects.filter(session__isnull=True, amount=100).exists())

    def test_combo_helper_counts_only_purchased_units(self):
        self.assertEqual(_purchase_pause_allowance(self.plan, 5), 75)
        combo = calculate_combo_for_amount(42)
        self.assertEqual(_purchase_pause_allowance(combo['highest_plan'], combo=combo), 36)

    def test_failed_extension_save_does_not_grant_pauses_or_consume_coins(self):
        target = self.session(pause_count=2)
        event = CoinEvent.objects.create(mac_address=self.mac, amount=100, denomination=20)
        with patch('sessions_app.views.Session.save', side_effect=RuntimeError('simulated save failure')):
            response = self.client.post('/api/session/extend-paid/',
                {'mac_address': self.mac, 'plan_id': self.plan.pk}, format='json')
        self.assertEqual(response.status_code, 500)
        target.refresh_from_db()
        event.refresh_from_db()
        self.assertIsNone(target.pause_limit)
        self.assertEqual(target.pause_count, 2)
        self.assertEqual(target.duration_minutes_purchased, 300)
        self.assertIsNone(event.session_id)
