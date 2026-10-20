from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import subprocess
from unittest.mock import patch

from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from . import access_points as monitor


NOW = datetime(2026, 10, 16, 4, 0, tzinfo=timezone.utc)
MAC = monitor.ACCESS_POINTS[0][2]


class AddressAndProbeTests(SimpleTestCase):
    def test_resolves_mac_not_hostname_and_follows_dhcp_change(self):
        for address in ('10.10.10.106', '10.10.10.128'):
            with self.subTest(address=address), patch.object(monitor.LEASE_FILE.__class__, 'read_text',
                    return_value=f'{int(NOW.timestamp())+3600} {MAC.lower()} {address} * *\n'), patch.object(
                    monitor.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='[]')):
                self.assertEqual(monitor._resolve_addresses(NOW)[MAC], address)

    def test_expired_invalid_and_outside_lan_leases_are_ignored(self):
        lines = '\n'.join((f'1 {MAC} 10.10.10.106 * *', f'bad {MAC} 10.10.10.106 * *',
                           f'0 {MAC} 8.8.8.8 * *', f'0 {MAC} 10.10.10.1 * *',
                           f'0 {MAC} 10.10.10.255 * *', 'malformed'))
        with patch.object(monitor.LEASE_FILE.__class__, 'read_text', return_value=lines), patch.object(
                monitor.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='[]')):
            self.assertEqual(monitor._resolve_addresses(NOW), {})

    def test_permanent_lease_is_accepted(self):
        with patch.object(monitor.LEASE_FILE.__class__, 'read_text', return_value=f'0 {MAC} 10.10.10.106 * *'), patch.object(
                monitor.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='[]')):
            self.assertEqual(monitor._resolve_addresses(NOW)[MAC], '10.10.10.106')

    def test_neighbor_fallback_is_only_an_address_candidate(self):
        neighbor = '[{"lladdr":"%s","dst":"10.10.10.209","state":["STALE"]}]' % MAC.lower()
        with patch.object(monitor.LEASE_FILE.__class__, 'read_text', side_effect=OSError), patch.object(
                monitor.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=neighbor)):
            self.assertEqual(monitor._resolve_addresses(NOW), {MAC: '10.10.10.209'})

    def test_missing_discovery_tools_and_files_do_not_raise(self):
        with patch.object(monitor.LEASE_FILE.__class__, 'read_text', side_effect=OSError), patch.object(
                monitor.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertEqual(monitor._resolve_addresses(NOW), {})

    def test_fresh_reply_must_match_mac_and_uses_bounded_interface_probe(self):
        with patch.object(monitor.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=f'Unicast reply [ {MAC} ]')) as run:
            self.assertIsNone(monitor._probe(MAC, '10.10.10.106'))
            run.assert_called_once_with(['arping', '-I', 'usblan0', '-c', '1', '-w', '1', '10.10.10.106'],
                                        capture_output=True, text=True, timeout=2)
        with patch.object(monitor.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=f'Unicast reply [{MAC.lower()}]')):
            self.assertTrue(monitor._probe(MAC, '10.10.10.106'))

    def test_wrong_identity_tool_failure_and_timeout_are_unknown(self):
        for result in (SimpleNamespace(returncode=0, stdout='reply [AA:BB:CC:DD:EE:FF]'),
                       SimpleNamespace(returncode=2, stdout=''), SimpleNamespace(returncode=0, stdout='')):
            with self.subTest(result=result), patch.object(monitor.subprocess, 'run', return_value=result):
                self.assertIsNone(monitor._probe(MAC, '10.10.10.106'))
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('arping', 2)):
            with self.subTest(error=error), patch.object(monitor.subprocess, 'run', side_effect=error):
                self.assertIsNone(monitor._probe(MAC, '10.10.10.106'))

    def test_completed_no_reply_is_false_and_missing_address_does_not_probe(self):
        with patch.object(monitor.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='')) as run:
            self.assertIsNone(monitor._probe(MAC, None))
            run.assert_not_called()
            self.assertFalse(monitor._probe(MAC, '10.10.10.106'))


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                                      'LOCATION': 'ap-monitor-tests'}})
class AccessPointMonitorTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        clock = patch.object(monitor.timezone, 'now', return_value=NOW)
        self.clock = clock.start(); self.addCleanup(clock.stop)
        resolver = patch.object(monitor, '_resolve_addresses', return_value={
            MAC: '10.10.10.106', monitor.ACCESS_POINTS[1][2]: '10.10.10.209'})
        self.resolver = resolver.start(); self.addCleanup(resolver.stop)
        probe = patch.object(monitor, '_probe', return_value=True)
        self.probe = probe.start(); self.addCleanup(probe.stop)

    def scan(self, result, advance=30):
        self.clock.return_value += timedelta(seconds=advance)
        self.probe.return_value = result
        return monitor.get_access_point_status()['access_points'][0]

    def test_initial_success_and_shared_thirty_second_cache(self):
        result = monitor.get_access_point_status()
        self.assertEqual(len(result['access_points']), 2)
        self.assertEqual(result['access_points'][0]['status'], 'reachable')
        self.assertEqual(result['access_points'][0]['last_seen'], NOW.isoformat())
        monitor.get_access_point_status()
        self.assertEqual(self.probe.call_count, 2)

    def test_three_misses_required_and_recovery_preserves_last_seen(self):
        monitor.get_access_point_status()
        for expected in ('unknown', 'unknown', 'unreachable'):
            row = self.scan(False)
            self.assertEqual(row['status'], expected)
            self.assertEqual(row['last_seen'], NOW.isoformat())
        row = self.scan(True)
        self.assertEqual(row['status'], 'reachable')
        self.assertEqual(row['misses'], 0)
        self.assertEqual(row['last_seen'], self.clock.return_value.isoformat())

    def test_unknown_result_resets_misses(self):
        self.scan(False); self.scan(False); self.scan(None)
        self.assertEqual(self.scan(False)['misses'], 1)

    def test_long_gap_and_new_ip_reset_misses(self):
        self.scan(False); self.scan(False)
        self.assertEqual(self.scan(False, advance=91)['misses'], 1)
        self.scan(False)
        self.resolver.return_value = {MAC: '10.10.10.150'}
        row = self.scan(False)
        self.assertEqual(row['misses'], 1)
        self.assertEqual(row['ip'], '10.10.10.150')

    def test_unresolved_ip_stays_unknown(self):
        self.resolver.return_value = {}
        self.probe.side_effect = lambda mac, address: None if address is None else True
        row = self.scan(None)
        self.assertEqual(row['status'], 'unknown')
        self.assertIn('Address not found', row['reason'])

    def test_concurrent_request_does_not_scan_and_stale_data_is_unknown(self):
        monitor.get_access_point_status()
        self.clock.return_value += timedelta(seconds=100)
        cache.add(monitor.LOCK_KEY, 'other-owner', 10)
        self.probe.reset_mock()
        rows = monitor.get_access_point_status()['access_points']
        self.probe.assert_not_called()
        self.assertEqual(rows[0]['status'], 'unknown')
        self.assertEqual(rows[0]['last_seen'], NOW.isoformat())
        self.assertEqual(cache.get(monitor.LOCK_KEY), 'other-owner')

    def test_cache_outage_is_unknown_and_does_not_probe(self):
        with patch.object(monitor.cache, 'get', side_effect=RuntimeError('cache unavailable')):
            rows = monitor.get_access_point_status()['access_points']
        self.assertEqual(rows[0]['status'], 'unknown')
        self.probe.assert_not_called()

    def test_failed_cache_save_does_not_claim_reliable_status(self):
        with patch.object(monitor.cache, 'set', side_effect=RuntimeError('cache unavailable')):
            rows = monitor.get_access_point_status()['access_points']
        self.assertEqual(rows[0]['status'], 'unknown')
        self.assertIsNone(cache.get(monitor.LOCK_KEY))


class AccessPointApiTests(SimpleTestCase):
    @patch('sessions_app.power_recovery.ensure_power_recovery', return_value=0)
    def test_only_staff_may_probe_and_response_is_private(self, recovery):
        client = APIClient()
        with patch('dashboard.access_points.get_access_point_status', return_value={'access_points': []}) as check:
            self.assertIn(client.get('/api/dashboard/access-points/').status_code, (401, 403))
            client.force_authenticate(SimpleNamespace(is_authenticated=True, is_staff=False))
            self.assertIn(client.get('/api/dashboard/access-points/').status_code, (401, 403))
            check.assert_not_called()
            client.force_authenticate(SimpleNamespace(is_authenticated=True, is_staff=True))
            response = client.get('/api/dashboard/access-points/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            self.assertEqual(client.post('/api/dashboard/access-points/').status_code, 405)

    def test_overview_replaces_roi_progress_with_two_vertical_ap_states(self):
        html = render_to_string('dashboard/overview.html', {})
        hero = html.split('<!-- Hero Section', 1)[1].split('<!-- System Resources Strip -->', 1)[0]
        network = html.split('<h3 class="chart-title">Network Overview</h3>', 1)[1].split('<!-- Recent Sessions Table -->', 1)[0]
        self.assertIn('id="access-point-monitor"', hero)
        self.assertIn('ap-monitor-card', hero)
        self.assertIn('class="ap-monitor-list"', hero)
        self.assertEqual(html.count('id="access-point-monitor"'), 1)
        self.assertNotIn('id="access-point-monitor"', network)
        self.assertNotIn('ROI Progress', html)
        self.assertNotIn('id="roi-progress"', html)
        self.assertIn('id="nav-roi"', html)  # The separate ROI page stays available.
        for index, (key, name, mac) in enumerate(monitor.ACCESS_POINTS, 1):
            self.assertEqual(html.count(f'data-ap-id="{key}"'), 1)
            self.assertIn(f'EAP225 #{index}', hero)
            self.assertNotIn(mac, html)
        for removed in ('data-ap="reason"', 'data-ap="ip"', 'data-ap="checked"', 'data-ap="seen"',
                        'Last checked:', 'Last seen:', 'Fresh reply from this AP MAC',
                        'Checks every 30 seconds while this dashboard is open'):
            self.assertNotIn(removed, html)
        self.assertEqual(hero.count('class="ap-status"'), 2)
        self.assertIn('not a power or WiFi health guarantee', hero)
        self.assertIn('access_points.js', html)
        self.assertIn('access_points.js?v=2', html)

    def test_ap_card_keeps_stacked_layout_and_network_statistics(self):
        html = render_to_string('dashboard/overview.html', {})
        self.assertIn('.ap-monitor-list { display: flex; flex-direction: column;', html)
        self.assertIn('flex-wrap: wrap;', html)
        for field in ('live-active-users', 'live-total-bandwidth', 'sys-internet-text', 'sys-cpu-temp'):
            self.assertIn(f'id="{field}"', html)
