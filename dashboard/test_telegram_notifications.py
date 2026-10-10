from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from dashboard.models import IssueReport, SystemSettings, TelegramNotification
from dashboard.notification_signals import enqueue, ticket_after, security_after
from dashboard.telegram_notifications import deliver_pending_notifications
from sessions_app.models import SuspiciousDevice

TEST_TOKEN = '123456789:' + 'TEST_ONLY_' * 4


class TelegramNotificationFixture:
    def setUp(self):
        self.settings = SystemSettings.get_settings()
        self.settings.enable_telegram_bot = True
        self.settings.telegram_bot_token = TEST_TOKEN
        self.settings.telegram_admin_chat_id = '12345678'
        self.settings.telegram_notify_tickets = True
        self.settings.telegram_notify_security = True
        self.settings.save()

    def ticket(self, **kwargs):
        data = {'mac_address': 'AA:BB:CC:DD:EE:01', 'category': 'other',
                'message': 'Please check the connection', 'admin_notes': 'PRIVATE_INTERNAL_NOTE'}
        data.update(kwargs)
        return IssueReport.objects.create(**data)

    def incident(self):
        return SuspiciousDevice.record_incident('AA:BB:CC:DD:EE:02', '10.10.10.22', 'mac_ip_conflict', 'Address mismatch')


class TelegramEventTests(TelegramNotificationFixture, TestCase):

    def test_new_ticket_enqueues_once_without_network_or_internal_notes(self):
        with patch('dashboard.telegram_bot.urllib.request.urlopen') as network:
            ticket = self.ticket()
        network.assert_not_called()
        event = TelegramNotification.objects.get()
        self.assertEqual(event.event_key, f'ticket:{ticket.pk}:created')
        self.assertEqual(event.kind, 'ticket')
        self.assertIn('New Support Ticket', event.body)
        self.assertNotIn('PRIVATE_INTERNAL_NOTE', event.body)
        self.assertIn('Asia/Manila', event.body)

    def test_new_security_alert_enqueues_once(self):
        incident = self.incident()
        event = TelegramNotification.objects.get()
        self.assertEqual(event.event_key, f'security:{incident.pk}:created')
        self.assertIn('mac_ip_conflict', event.body)
        self.assertIn('10.10.10.22', event.body)

    def test_customer_submission_and_duplicate_click_queue_only_one_alert(self):
        cache.clear()
        data = {'category': 'other', 'message': 'Notification integration test', 'mac_address': 'AA:BB:CC:DD:EE:01'}
        with patch('dashboard.telegram_bot.urllib.request.urlopen') as network:
            first = self.client.post('/api/report-issue/', data, content_type='application/json')
            duplicate = self.client.post('/api/report-issue/', data, content_type='application/json')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(IssueReport.objects.count(), 1)
        self.assertEqual(TelegramNotification.objects.count(), 1)
        network.assert_not_called()
        cache.clear()

    def test_repeated_unresolved_detections_do_not_spam(self):
        incident = self.incident()
        for count in range(5):
            self.incident()
        incident.refresh_from_db()
        self.assertEqual(incident.detection_count, 6)
        self.assertEqual(TelegramNotification.objects.count(), 1)

    def test_cleared_or_false_positive_incident_can_alert_when_reopened(self):
        incident = self.incident()
        incident.mark_cleared(by='operator')
        self.incident()
        incident.refresh_from_db()
        incident.mark_false_positive(by='operator')
        self.incident()
        self.assertEqual(TelegramNotification.objects.count(), 3)
        self.assertEqual(TelegramNotification.objects.filter(body__contains='Reopened').count(), 2)

    def test_operator_block_does_not_create_new_detection_alert(self):
        SuspiciousDevice.objects.create(mac_address='AA:BB:CC:DD:EE:03', status='blocked', is_blocked=True)
        self.assertFalse(TelegramNotification.objects.exists())

    def test_reply_and_status_changes_alert_but_unchanged_saves_do_not(self):
        ticket = self.ticket()
        ticket.admin_reply = 'Your credit has been restored.'
        ticket.status = 'answered'
        ticket.save()
        self.assertEqual(TelegramNotification.objects.count(), 2)
        self.assertIn('Your credit has been restored', TelegramNotification.objects.last().body)
        ticket.save()
        self.assertEqual(TelegramNotification.objects.count(), 2)
        ticket.status = 'resolved'
        ticket.save(update_fields=['status'])
        self.assertEqual(TelegramNotification.objects.count(), 3)

    def test_viewed_markers_and_internal_note_changes_do_not_alert(self):
        ticket = self.ticket()
        ticket.user_viewed_at = timezone.now()
        ticket.admin_notes = 'PRIVATE_SECOND_NOTE'
        ticket.save(update_fields=['user_viewed_at', 'admin_notes'])
        ticket.save()
        self.assertEqual(TelegramNotification.objects.count(), 1)

    def test_partial_save_ignores_unpersisted_reply(self):
        ticket = self.ticket()
        ticket.admin_reply = 'UNSAVED_REPLY'
        ticket.save(update_fields=['status'])
        self.assertEqual(TelegramNotification.objects.count(), 1)
        ticket.status = 'resolved'
        ticket.save(update_fields=['status'])
        self.assertNotIn('UNSAVED_REPLY', TelegramNotification.objects.last().body)

    def test_disabled_category_or_master_does_not_enqueue(self):
        self.settings.telegram_notify_tickets = False
        self.settings.telegram_notify_security = False
        self.settings.save()
        self.ticket()
        self.incident()
        self.assertFalse(TelegramNotification.objects.exists())
        self.settings.telegram_notify_tickets = True
        self.settings.telegram_notify_security = True
        self.settings.enable_telegram_bot = False
        self.settings.save()
        self.ticket()
        SuspiciousDevice.record_incident('AA:BB:CC:DD:EE:04')
        self.assertFalse(TelegramNotification.objects.exists())

    def test_token_like_customer_text_redacted_from_outbox(self):
        self.ticket(message='I pasted ' + TEST_TOKEN)
        self.assertNotIn(TEST_TOKEN, TelegramNotification.objects.get().body)
        self.assertIn('[redacted bot token]', TelegramNotification.objects.get().body)

    def test_outbox_is_rolled_back_with_originating_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.ticket()
                self.assertEqual(TelegramNotification.objects.count(), 1)
                raise RuntimeError('rollback')
        self.assertFalse(TelegramNotification.objects.exists())
        self.assertFalse(IssueReport.objects.exists())

    def test_notification_failure_does_not_fail_ticket_creation_or_leak_error(self):
        with patch('django.db.models.query.QuerySet.get_or_create', side_effect=ValueError(TEST_TOKEN)):
            with self.assertLogs('dashboard.notification_signals', level='ERROR') as logs:
                ticket = self.ticket()
        self.assertTrue(IssueReport.objects.filter(pk=ticket.pk).exists())
        self.assertNotIn(TEST_TOKEN, str(logs.output))

    def test_unique_event_key_prevents_duplicate_queue_rows(self):
        enqueue('ticket', 'same-event', 'Test', 'default')
        enqueue('ticket', 'same-event', 'Test', 'default')
        self.assertEqual(TelegramNotification.objects.count(), 1)

    def test_fixture_import_does_not_send_historical_alerts(self):
        ticket_after(IssueReport, IssueReport(message='Old fixture'), True, True, 'default', None)
        security_after(SuspiciousDevice, SuspiciousDevice(mac_address='AA:BB:CC:DD:EE:01'), True, True, 'default', None)
        self.assertFalse(TelegramNotification.objects.exists())


class TelegramDeliveryTests(TelegramNotificationFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.send_patch = patch('dashboard.telegram_notifications.send_telegram_message', return_value=True)
        self.send = self.send_patch.start()
        self.addCleanup(self.send_patch.stop)

    def test_successful_delivery_marks_sent_and_never_resends_on_next_poll(self):
        self.ticket()
        self.assertEqual(deliver_pending_notifications(), 1)
        self.assertEqual(deliver_pending_notifications(), 0)
        event = TelegramNotification.objects.get()
        self.assertEqual(event.attempts, 1)
        self.assertIsNotNone(event.sent_at)
        self.assertIsNone(event.lease_until)
        self.send.assert_called_once_with(event.body, parse_mode=None)

    def test_failed_send_persists_and_retries_after_backoff(self):
        self.incident()
        self.send.return_value = False
        now = timezone.now()
        with patch('dashboard.telegram_notifications.timezone.now', return_value=now):
            self.assertEqual(deliver_pending_notifications(), 0)
        event = TelegramNotification.objects.get()
        self.assertIsNone(event.sent_at)
        self.assertEqual(event.next_attempt_at, now + timedelta(seconds=30))
        self.assertEqual(deliver_pending_notifications(), 0)
        self.send.return_value = True
        with patch('dashboard.telegram_notifications.timezone.now', return_value=now + timedelta(seconds=31)):
            self.assertEqual(deliver_pending_notifications(), 1)

    def test_live_lease_skips_event_and_expired_lease_recovers_after_crash(self):
        self.ticket()
        now = timezone.now()
        TelegramNotification.objects.update(lease_until=now + timedelta(minutes=2))
        self.assertEqual(deliver_pending_notifications(), 0)
        self.send.assert_not_called()
        with patch('dashboard.telegram_notifications.timezone.now', return_value=now + timedelta(minutes=3)):
            self.assertEqual(deliver_pending_notifications(), 1)

    def test_concurrent_delivery_cannot_claim_same_live_lease(self):
        self.ticket()
        nested = []
        def send(*args, **kwargs):
            nested.append(deliver_pending_notifications())
            return True
        self.send.side_effect = send
        self.assertEqual(deliver_pending_notifications(), 1)
        self.assertEqual(nested, [0])
        self.assertEqual(self.send.call_count, 1)

    def test_disabled_category_cancels_pending_alert_without_network(self):
        self.incident()
        self.settings.telegram_notify_security = False
        self.settings.save()
        self.assertEqual(deliver_pending_notifications(), 0)
        self.assertIsNotNone(TelegramNotification.objects.get().cancelled_at)
        self.send.assert_not_called()

    def test_missing_credentials_or_master_off_keeps_queue_without_network(self):
        self.ticket()
        self.settings.telegram_bot_token = ''
        self.settings.save()
        self.assertEqual(deliver_pending_notifications(), 0)
        self.assertEqual(TelegramNotification.objects.get().attempts, 0)
        self.send.assert_not_called()

    def test_sender_exception_does_not_lose_pending_message(self):
        self.ticket()
        self.send.side_effect = RuntimeError(TEST_TOKEN)
        self.assertEqual(deliver_pending_notifications(), 0)
        self.assertIsNone(TelegramNotification.objects.get().sent_at)

    def test_bounded_batch_and_long_outage_retry(self):
        for count in range(5):
            self.ticket()
        self.assertEqual(deliver_pending_notifications(limit=2), 2)
        self.assertEqual(TelegramNotification.objects.filter(sent_at__isnull=True).count(), 3)
        TelegramNotification.objects.filter(sent_at__isnull=True).update(attempts=100, next_attempt_at=timezone.now()-timedelta(days=3))
        self.send.return_value = False
        self.assertEqual(deliver_pending_notifications(), 0)
        self.assertTrue(TelegramNotification.objects.filter(attempts=101, sent_at__isnull=True).exists())


class TelegramNotificationSettingsTests(TestCase):
    def setUp(self):
        admin = get_user_model().objects.create_superuser('notify_admin', 'notify@example.com', 'test-pass')
        self.client.force_login(admin)
        self.settings = SystemSettings.get_settings()
        self.settings.telegram_bot_token = TEST_TOKEN
        self.settings.telegram_admin_chat_id = '12345678'
        self.settings.save()

    def test_settings_shows_security_and_support_but_not_midnight(self):
        response = self.client.get('/iconnect-ops/settings/')
        self.assertContains(response, 'Security Alerts')
        self.assertContains(response, 'Support Ticket Alerts')
        self.assertNotContains(response, 'Midnight Sales Closeout')
        self.assertNotContains(response, TEST_TOKEN)
        self.assertFalse(self.settings.telegram_notify_daily_summary)

    def test_switches_save_without_resetting_credentials_or_enabling_midnight(self):
        with patch('sessions_app.iptables.apply_network_settings'):
            response = self.client.post('/iconnect-ops/settings/', {
                'enable_telegram_bot': 'on', 'telegram_bot_token': '',
                'telegram_notify_tickets': 'on', 'telegram_notify_security': 'on',
                'telegram_notify_daily_summary': 'on',
            })
        self.assertEqual(response.status_code, 302)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.telegram_bot_token, TEST_TOKEN)
        self.assertTrue(self.settings.telegram_notify_security)
        self.assertTrue(self.settings.telegram_notify_tickets)
        self.assertFalse(self.settings.telegram_notify_daily_summary)
