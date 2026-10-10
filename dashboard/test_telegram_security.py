import io
import json
import gzip
import os
import urllib.error
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase

from dashboard.models import SystemSettings
from dashboard import telegram_bot as bot
from dashboard.management.commands.run_telegram_bot import dispatch_updates


# Deliberately synthetic and assembled: no real credential belongs in tests.
TEST_TOKEN = '123456789:' + 'TEST_ONLY_' * 4
ADMIN_ID = '12345678'


class TelegramSecurityTests(TestCase):
    def setUp(self):
        self.settings = SystemSettings.get_settings()
        self.settings.telegram_bot_token = TEST_TOKEN
        self.settings.telegram_admin_chat_id = ADMIN_ID
        self.settings.enable_telegram_bot = True
        self.settings.save()
        self.cfg = bot.get_telegram_config()

    def test_configuration_error_fails_closed_without_secret_in_log(self):
        with patch.object(SystemSettings, 'get_settings', side_effect=RuntimeError(TEST_TOKEN)):
            with self.assertLogs(bot.logger, level='ERROR') as logs:
                cfg = bot.get_telegram_config()
        self.assertFalse(cfg['enabled'])
        self.assertEqual(cfg['token'], '')
        self.assertEqual(cfg['chat_id'], '')
        self.assertNotIn(TEST_TOKEN, str(logs.output))

    def test_incomplete_or_disabled_config_not_ready(self):
        for field, value in [('token', ''), ('chat_id', ''), ('enabled', False),
                             ('token', 'invalid'), ('chat_id', '-12345678')]:
            with self.subTest(field=field):
                self.assertFalse(bot.telegram_config_ready(dict(self.cfg, **{field: value})))

    def test_no_network_for_missing_admin_disabled_or_wrong_recipient(self):
        with patch.object(bot, 'get_telegram_config') as config, patch.object(bot.urllib.request, 'urlopen') as request:
            for cfg in [dict(self.cfg, chat_id=''), dict(self.cfg, enabled=False), self.cfg]:
                config.return_value = cfg
                self.assertFalse(bot.send_telegram_message('test', chat_id='98765432'))
                self.assertFalse(bot.send_telegram_document(__file__, chat_id='98765432'))
            request.assert_not_called()

    def test_unconfigured_unauthorized_and_group_commands_do_not_execute(self):
        with patch.object(bot, 'get_telegram_config') as config, patch.object(bot, 'send_telegram_message') as send:
            for cfg, sender, chat in [(dict(self.cfg, chat_id=''), ADMIN_ID, None),
                                      (dict(self.cfg, enabled=False), ADMIN_ID, None),
                                      (self.cfg, '98765432', None), (self.cfg, ADMIN_ID, '-12345678')]:
                config.return_value = cfg
                bot.handle_telegram_command('/help', sender, chat_id=chat)
            send.assert_not_called()

    def test_authorized_help_works(self):
        with patch.object(bot, 'send_telegram_message') as send:
            bot.handle_telegram_command('/help', ADMIN_ID, chat_id=ADMIN_ID)
        send.assert_called_once()

    def test_authorized_message_and_document_use_configured_admin(self):
        with patch.object(bot.urllib.request, 'urlopen') as request:
            request.return_value.__enter__.return_value.status = 200
            self.assertTrue(bot.send_telegram_message('test'))
            self.assertTrue(bot.send_telegram_document(__file__))
        self.assertEqual(request.call_count, 2)

    def test_markdown_400_retries_plain_text_without_logging_secret(self):
        error = urllib.error.HTTPError('https://api.telegram.org/bot' + TEST_TOKEN, 400, TEST_TOKEN, {}, None)
        with patch.object(bot.urllib.request, 'urlopen') as request:
            response = request.return_value
            response.__enter__.return_value.status = 200
            request.side_effect = [error, response]
            with self.assertLogs(bot.logger, level='ERROR') as logs:
                self.assertTrue(bot.send_telegram_message('test'))
        self.assertEqual(request.call_count, 2)
        self.assertNotIn(TEST_TOKEN, str(logs.output))

    def test_message_error_does_not_log_token_or_url(self):
        error = urllib.error.URLError('https://api.telegram.org/bot' + TEST_TOKEN)
        with patch.object(bot.urllib.request, 'urlopen', side_effect=error) as request:
            with self.assertLogs(bot.logger, level='ERROR') as logs:
                self.assertFalse(bot.send_telegram_message('test'))
        self.assertNotIn(TEST_TOKEN, str(logs.output))
        self.assertEqual(request.call_count, 1)

    def test_document_error_does_not_log_token(self):
        with patch.object(bot.urllib.request, 'urlopen', side_effect=RuntimeError(TEST_TOKEN)):
            with self.assertLogs(bot.logger, level='ERROR') as logs:
                self.assertFalse(bot.send_telegram_document(__file__))
        self.assertNotIn(TEST_TOKEN, str(logs.output))

    def test_backup_removes_bot_token_but_retains_session_records(self):
        records = [{'model': 'dashboard.systemsettings', 'fields': {'telegram_bot_token': TEST_TOKEN}},
                   {'model': 'sessions_app.session', 'pk': 7, 'fields': {'status': 'paused'}}]
        result = json.loads(bot.redact_telegram_backup(json.dumps(records)))
        self.assertEqual(result[0]['fields']['telegram_bot_token'], '')
        self.assertEqual(result[1], records[1])
        self.assertNotIn(TEST_TOKEN, json.dumps(result))

    def test_backup_command_uses_private_directory_redacts_and_cleans_up(self):
        records = [{'model': 'dashboard.systemsettings', 'fields': {'telegram_bot_token': TEST_TOKEN}}]
        captured = []

        def dump(*args, **kwargs):
            kwargs['stdout'].write(json.dumps(records))

        def document(filename, **kwargs):
            path = Path(filename)
            captured.append(path)
            if os.name == 'posix':
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            with gzip.open(filename, 'rt') as stream:
                self.assertNotIn(TEST_TOKEN, stream.read())
            return True

        with patch('tempfile.gettempdir', return_value=str(Path.cwd())), patch('django.core.management.call_command', side_effect=dump), patch.object(bot, 'send_telegram_message'), patch.object(bot, 'send_telegram_document', side_effect=document), patch.object(bot.logger, 'error') as error:
            bot.handle_telegram_command('/backup', ADMIN_ID, chat_id=ADMIN_ID)
        self.assertFalse(error.called, str(error.call_args))
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0].exists())
        self.assertFalse(captured[0].parent.exists())

    def test_poller_refuses_missing_configuration_without_starting_threads(self):
        from dashboard.management.commands import run_telegram_bot as poller
        with patch.object(poller, 'get_telegram_config', return_value=dict(self.cfg, token='')):
            with patch.object(poller.threading, 'Thread') as thread:
                with self.assertRaises(CommandError):
                    call_command('run_telegram_bot', stdout=io.StringIO(), stderr=io.StringIO())
                thread.assert_not_called()

    def update(self, **changes):
        message = {'text': '/help', 'date': 101, 'from': {'id': int(ADMIN_ID), 'is_bot': False},
                   'chat': {'id': int(ADMIN_ID), 'type': 'private'}}
        message.update(changes)
        return {'update_id': 10, 'message': message}

    def test_poller_only_dispatches_fresh_private_admin_messages(self):
        from dashboard.management.commands import run_telegram_bot as poller
        updates = [self.update(date=99), self.update(chat={'id': -12345678, 'type': 'group'}),
                   self.update(**{'from': {'id': 98765432}}), self.update()]
        with patch.object(poller, 'handle_telegram_command') as handle:
            self.assertEqual(dispatch_updates(updates, self.cfg, 0, 100), 11)
        handle.assert_called_once_with('/help', int(ADMIN_ID), 'Operator', chat_id=int(ADMIN_ID))

    def test_poller_drops_response_after_token_rotation_or_disable(self):
        from dashboard.management.commands import run_telegram_bot as poller
        for cfg in [dict(self.cfg, token='123456789:' + 'NEW_TEST_' * 4), dict(self.cfg, enabled=False)]:
            with patch.object(poller, 'get_telegram_config', return_value=cfg), patch.object(poller, 'handle_telegram_command') as handle:
                dispatch_updates([self.update()], self.cfg, 0, 100)
                handle.assert_not_called()


class TelegramSettingsSecurityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('telegram_admin', 'admin@example.com', 'test-password')
        self.client.force_login(self.user)
        self.settings = SystemSettings.get_settings()
        self.settings.telegram_bot_token = TEST_TOKEN
        self.settings.telegram_admin_chat_id = ADMIN_ID
        self.settings.save()

    def post(self, data):
        with patch('sessions_app.iptables.apply_network_settings'):
            return self.client.post('/iconnect-ops/settings/', data, follow=True)

    def test_saved_token_never_rendered_in_html(self):
        response = self.client.get('/iconnect-ops/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, TEST_TOKEN)
        self.assertContains(response, 'A token is saved')

    def test_blank_field_keeps_saved_token(self):
        self.post({'telegram_bot_token': '', 'telegram_admin_chat_id': ADMIN_ID, 'enable_telegram_bot': 'on'})
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.telegram_bot_token, TEST_TOKEN)

    def test_explicit_remove_clears_token(self):
        self.post({'telegram_bot_token': '', 'clear_telegram_token': 'on'})
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.telegram_bot_token, '')
        self.assertFalse(self.settings.enable_telegram_bot)

    def test_enabling_without_credentials_rejected(self):
        self.settings.telegram_bot_token = ''
        self.settings.enable_telegram_bot = False
        self.settings.save()
        response = self.post({'telegram_bot_token': '', 'telegram_admin_chat_id': '', 'enable_telegram_bot': 'on'})
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.enable_telegram_bot)
        self.assertContains(response, 'Configure both a fresh Telegram bot token')

    def test_group_admin_id_rejected(self):
        response = self.post({'telegram_bot_token': '', 'telegram_admin_chat_id': '-12345678'})
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.telegram_admin_chat_id, ADMIN_ID)
        self.assertContains(response, 'not a group ID')
