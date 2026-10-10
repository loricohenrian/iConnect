from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from . import iptables


@override_settings(PISONET_GPIO_SIMULATION=False, PISONET_DNS_ONLY_PREAUTH=False)
class VerifiedAccessGrantTests(SimpleTestCase):
    mac = '02:00:00:00:00:01'

    def setUp(self):
        self.forward = self.mock('is_device_allowed', side_effect=[False, True])
        self.nat = self.mock('_is_nat_bypass_set', side_effect=[False, True])
        self.add_nat = self.mock('_add_nat_bypass', return_value=True)
        self.run = self.mock('_run_command', return_value=True)
        self.flush = self.mock('_flush_conntrack')
        self.limit = self.mock('apply_bandwidth_limit')
        self.mock('_ensure_mangle_forward_rule')
        self.mock('_ensure_mangle_postrouting_rule')
        self.mock('_ensure_forward_rule')

    def mock(self, name, **kwargs):
        patcher = patch(f'sessions_app.iptables.{name}', **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def grant(self):
        return iptables.allow_device(self.mac, rate_kbps=3072, upload_kbps=2048)

    def test_new_grant_checks_forward_and_nat_before_success(self):
        self.assertTrue(self.grant())
        self.assertEqual(self.forward.call_count, 2)
        self.assertEqual(self.nat.call_count, 2)
        self.flush.assert_called_once_with(self.mac)
        self.limit.assert_called_once_with(self.mac, rate_kbps=3072, upload_kbps=2048)

    def test_failed_nat_add_rolls_back_new_forward_grant(self):
        self.add_nat.return_value = False
        self.assertFalse(self.grant())
        self.run.assert_any_call(
            ['iptables', '-D', 'FORWARD', '-m', 'mac', '--mac-source', self.mac, '-j', 'ACCEPT'],
            ignore_errors=True,
        )
        self.flush.assert_not_called()
        self.limit.assert_not_called()

    def test_forward_insertion_failure_does_not_attempt_nat(self):
        self.run.return_value = False
        self.assertFalse(self.grant())
        self.add_nat.assert_not_called()

    def test_successful_command_but_missing_forward_rule_is_not_success(self):
        self.forward.side_effect = [False, False]
        self.assertFalse(self.grant())
        self.limit.assert_not_called()

    def test_successful_command_but_missing_nat_rule_is_not_success(self):
        self.nat.side_effect = [False, False]
        self.assertFalse(self.grant())
        self.limit.assert_not_called()

    def test_existing_forward_rule_does_not_hide_nat_failure(self):
        self.forward.side_effect = None
        self.forward.return_value = True
        self.add_nat.return_value = False
        self.assertFalse(self.grant())
        self.run.assert_not_called()  # Do not discard a pre-existing grant here.

    def test_existing_complete_access_does_not_flush_connections(self):
        self.forward.side_effect = None
        self.forward.return_value = True
        self.nat.side_effect = None
        self.nat.return_value = True
        self.assertTrue(self.grant())
        self.run.assert_not_called()
        self.flush.assert_not_called()

    def test_repaired_existing_bypass_flushes_stale_redirects(self):
        self.forward.side_effect = None
        self.forward.return_value = True
        self.assertTrue(self.grant())
        self.flush.assert_called_once_with(self.mac)


class DeviceScopedConnectionCleanupTests(SimpleTestCase):
    def test_cleanup_never_flushes_other_customers_google_connections(self):
        with patch('sessions_app.iptables._get_device_ip', return_value='10.10.10.168'), \
                patch('sessions_app.iptables._run_command', return_value=True) as run:
            self.assertTrue(iptables._flush_conntrack('02:00:00:00:00:01'))
        self.assertEqual(run.call_count, 4)
        for call in run.call_args_list:
            self.assertIn('10.10.10.168', call.args[0])
            self.assertFalse(any('/' in arg for arg in call.args[0]))

    def test_unknown_device_ip_does_not_flush_unrelated_connections(self):
        with patch('sessions_app.iptables._get_device_ip', return_value=None), \
                patch('sessions_app.iptables._run_command') as run:
            self.assertFalse(iptables._flush_conntrack('02:00:00:00:00:01'))
        run.assert_not_called()
