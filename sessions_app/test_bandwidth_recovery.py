"""Saved usage must survive counter resets and competing dashboard/worker polls."""
from unittest.mock import patch
import subprocess

from django.core.cache import cache
from django.test import TestCase, SimpleTestCase, override_settings

from . import bandwidth
from .models import Session


class SavedBandwidthTests(TestCase):
    def setUp(self):
        self.session = Session.objects.create(
            mac_address='02:00:00:00:00:01', amount_paid=0,
            duration_minutes_purchased=180, status='active',
            initial_bandwidth_mb=500, bandwidth_used_mb=500)

    def sample(self, value, session=None):
        with patch('sessions_app.bandwidth.get_device_bandwidth_mb', return_value=value):
            return bandwidth.refresh_session_bandwidth_usage(session or self.session)

    def test_saved_total_survives_reset_then_continues(self):
        self.sample(0)
        self.sample(200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.bandwidth_used_mb, 700)

    def test_previous_session_cannot_erase_post_reset_usage(self):
        previous = self.session
        previous.status = 'expired'
        previous.save(update_fields=['status'])
        current = Session.objects.create(
            mac_address=previous.mac_address, amount_paid=0,
            duration_minutes_purchased=180, status='active',
            initial_bandwidth_mb=0, bandwidth_used_mb=1000)
        self.sample(600, current)
        current.refresh_from_db()
        self.assertEqual(current.bandwidth_used_mb, 1600)

    def test_failed_read_does_not_reset_anchor_or_double_count(self):
        self.assertFalse(self.sample(None))
        self.sample(501)
        self.session.refresh_from_db()
        self.assertEqual(self.session.bandwidth_used_mb, 501)
        self.assertEqual(self.session.initial_bandwidth_mb, 501)

    def test_small_increments_are_not_discarded_by_display_rounding(self):
        for index in range(1, 11):
            self.sample(500 + index * 0.01)
        self.session.refresh_from_db()
        self.assertAlmostEqual(self.session.bandwidth_used_mb, 500.1, places=6)

    def test_stale_instances_cannot_count_same_bytes_twice(self):
        stale = Session.objects.get(pk=self.session.pk)
        self.sample(501)
        self.assertFalse(self.sample(501, stale))
        self.sample(502, stale)
        self.sample(502)
        self.session.refresh_from_db()
        self.assertEqual(self.session.bandwidth_used_mb, 502)
        self.assertEqual(stale.bandwidth_used_mb, 502)

    def test_unknown_partial_reset_does_not_erase_or_recount_old_bytes(self):
        self.sample(100)
        self.sample(101)
        self.session.refresh_from_db()
        self.assertEqual(self.session.bandwidth_used_mb, 501)

    def test_unknown_anchor_is_established_without_inventing_usage(self):
        Session.objects.filter(pk=self.session.pk).update(initial_bandwidth_mb=None)
        self.sample(800)
        self.session.refresh_from_db()
        self.assertEqual(self.session.bandwidth_used_mb, 500)
        self.assertEqual(self.session.initial_bandwidth_mb, 800)

    def test_invalid_samples_do_not_corrupt_saved_usage(self):
        for value in (float('nan'), float('inf'), -1):
            self.assertFalse(self.sample(value))
        self.session.refresh_from_db()
        self.assertEqual(self.session.bandwidth_used_mb, 500)
        self.assertEqual(self.session.initial_bandwidth_mb, 500)

    def test_new_session_with_failed_initial_read_does_not_inherit_old_traffic(self):
        with patch('sessions_app.bandwidth.get_device_bandwidth_mb', return_value=None):
            new = Session.objects.create(
                mac_address=self.session.mac_address, amount_paid=0,
                duration_minutes_purchased=180, status='active')
        self.assertIsNone(new.initial_bandwidth_mb)
        self.sample(500, new)
        self.assertEqual(new.bandwidth_used_mb, 0)
        self.assertEqual(new.initial_bandwidth_mb, 500)


@override_settings(PISONET_GPIO_SIMULATION=False)
class CounterReadTests(SimpleTestCase):
    mac = '02:00:00:00:00:01'

    def test_missing_mac_is_not_a_zero_counter(self):
        with patch.object(bandwidth, 'get_iptables_byte_counters', return_value={}):
            self.assertIsNone(bandwidth.get_device_bandwidth_mb(self.mac))

    def test_device_read_retains_byte_precision(self):
        with patch.object(bandwidth, 'get_iptables_byte_counters', return_value={self.mac: 1}):
            self.assertEqual(bandwidth.get_device_bandwidth_mb(self.mac), 1 / 1048576)

    def test_failed_command_rejects_partial_snapshot(self):
        for source in ('FORWARD', 'POSTROUTING', 'tc'):
            def run(cmd, **kwargs):
                failed = source in cmd
                return subprocess.CompletedProcess(cmd, 1 if failed else 0, stdout='')
            with self.subTest(source=source), \
                    patch.object(bandwidth, '_get_mac_to_ip_map', return_value={}), \
                    patch.object(bandwidth, '_get_ip_to_mac_map', return_value={}), \
                    patch.object(bandwidth, '_get_lan_interface', return_value='br0'), \
                    patch.object(bandwidth.subprocess, 'run', side_effect=run):
                self.assertIsNone(bandwidth.get_iptables_byte_counters())

    def test_failed_counter_api_returns_empty_device_list(self):
        with patch.object(bandwidth, 'get_iptables_byte_counters', return_value=None):
            self.assertEqual(bandwidth.get_all_device_bandwidth_mb(), [])

    def test_command_timeout_is_unavailable_not_zero(self):
        with patch.object(bandwidth, '_get_mac_to_ip_map', return_value={}), \
                patch.object(bandwidth, '_get_ip_to_mac_map', return_value={}), \
                patch.object(bandwidth.subprocess, 'run', side_effect=subprocess.TimeoutExpired('iptables', 10)):
            self.assertIsNone(bandwidth.get_iptables_byte_counters())


class ThroughputSnapshotTests(SimpleTestCase):
    mac = '02:00:00:00:00:01'

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def sample(self, stamp, counters):
        with patch('time.time', return_value=stamp), \
                patch.object(bandwidth, 'get_iptables_byte_counters', return_value=counters):
            return bandwidth.get_live_throughput_mbps()

    def test_failed_read_keeps_last_good_snapshot(self):
        self.sample(100, {self.mac: 1000000})
        self.assertEqual(self.sample(105, None)['total_mbps'], 0)
        self.assertEqual(self.sample(110, {self.mac: 2000000})['total_mbps'], 0.8)

    def test_missing_device_then_reappearing_is_not_a_speed_spike(self):
        self.sample(100, {self.mac: 1000000})
        self.sample(105, {})
        self.assertEqual(self.sample(110, {self.mac: 2000000})['total_mbps'], 0)

    def test_counter_reset_never_produces_negative_speed(self):
        self.sample(100, {self.mac: 1000000})
        self.assertEqual(self.sample(105, {self.mac: 0})['total_mbps'], 0)
        self.assertEqual(self.sample(110, {self.mac: 1000000})['total_mbps'], 1.6)

    def test_rapid_polls_do_not_overwrite_comparable_snapshot(self):
        self.sample(100, {self.mac: 0})
        self.sample(100.1, {self.mac: 100000})
        self.assertEqual(self.sample(105, {self.mac: 1000000})['total_mbps'], 1.6)

    def test_cached_pre_reboot_snapshot_cannot_produce_false_speed(self):
        with patch('sessions_app.power_recovery.current_boot_id', return_value='boot-a'):
            self.sample(100, {self.mac: 1000000})
        with patch('sessions_app.power_recovery.current_boot_id', return_value='boot-b'):
            self.assertEqual(self.sample(105, {self.mac: 5000000})['total_mbps'], 0)
            self.assertEqual(self.sample(110, {self.mac: 6000000})['total_mbps'], 1.6)
