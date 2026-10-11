import io
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from .database_backup import DatabaseBackupError, create_database_backup


PG_CONFIG = {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'test_backup_db',
             'HOST': '127.0.0.1', 'PORT': '5432', 'USER': 'backup_user',
             'PASSWORD': 'test:only\\credential', 'OPTIONS': {'sslmode': 'require'}}


class NativeDatabaseBackupTests(SimpleTestCase):
    def fake_dump(self, args, **kwargs):
        self.assertEqual(args, ['pg_dump', '--format=custom', '--no-password', '--lock-wait-timeout=10s'])
        self.assertNotIn(PG_CONFIG['PASSWORD'], ' '.join(args))
        env = kwargs['env']
        self.assertEqual(env['PGDATABASE'], PG_CONFIG['NAME'])
        self.assertEqual(env['PGHOST'], PG_CONFIG['HOST'])
        self.assertEqual(env['PGPORT'], PG_CONFIG['PORT'])
        self.assertEqual(env['PGUSER'], PG_CONFIG['USER'])
        self.assertEqual(env['PGSSLMODE'], 'require')
        self.assertNotIn('PGPASSWORD', env)
        self.assertNotIn('PGSERVICE', env)
        self.passfile = Path(env['PGPASSFILE'])
        self.assertEqual(self.passfile.read_text(), '*:*:test_backup_db:backup_user:test\\:only\\\\credential\n')
        kwargs['stdout'].write(b'PGDMPtest archive')
        return subprocess.CompletedProcess(args, 0, stderr=b'')

    def test_postgres_uses_configured_connection_and_private_passfile(self):
        with patch.dict(os.environ, {'PGDATABASE': 'wrong_db', 'PGPASSWORD': 'inherited', 'PGSERVICE': 'wrong'}), \
                patch('dashboard.database_backup.subprocess.run', side_effect=self.fake_dump):
            archive, extension, mime = create_database_backup(PG_CONFIG)
        with archive:
            self.assertEqual(archive.read(), b'PGDMPtest archive')
        self.assertEqual(extension, 'dump')
        self.assertEqual(mime, 'application/octet-stream')
        self.assertFalse(self.passfile.exists())

    def test_postgres_failures_never_return_fallback_or_partial_archive(self):
        failures = [FileNotFoundError('private tool path'),
                    subprocess.TimeoutExpired(['pg_dump'], 40),
                    subprocess.CompletedProcess(['pg_dump'], 1, stderr=b'secret credentials')]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                archive = tempfile.TemporaryFile('w+b')
                with patch('dashboard.database_backup.tempfile.TemporaryFile', return_value=archive), \
                        patch('dashboard.database_backup.subprocess.run') as run:
                    if isinstance(failure, Exception):
                        run.side_effect = failure
                    else:
                        run.return_value = failure
                    with self.assertRaises(DatabaseBackupError) as raised:
                        create_database_backup(PG_CONFIG)
                self.assertTrue(archive.closed)
                self.assertNotIn('secret', str(raised.exception))
                self.assertNotIn('private tool path', str(raised.exception))

    def test_postgres_rejects_empty_or_invalid_archive(self):
        for content in (b'', b'SQLite format 3\x00', b'plain SQL'):
            def run(args, **kwargs):
                kwargs['stdout'].write(content)
                return subprocess.CompletedProcess(args, 0)
            with self.subTest(content=content), patch('dashboard.database_backup.subprocess.run', side_effect=run):
                with self.assertRaises(DatabaseBackupError):
                    create_database_backup(PG_CONFIG)

    def test_missing_name_and_multiline_password_fail_before_dump(self):
        for changes in ({'NAME': ''}, {'PASSWORD': 'one\ntwo'}):
            with patch('dashboard.database_backup.subprocess.run') as run:
                with self.assertRaises(DatabaseBackupError):
                    create_database_backup({**PG_CONFIG, **changes})
                run.assert_not_called()

    def test_unsupported_engine_fails_without_guessing_sqlite(self):
        with self.assertRaises(DatabaseBackupError):
            create_database_backup({'ENGINE': 'unknown', 'NAME': 'db.sqlite3'})

    def test_sqlite_snapshot_restores_committed_wal_data(self):
        with tempfile.TemporaryDirectory(prefix='iconnect-backup-test-') as directory:
            source_path = Path(directory) / 'actual.sqlite3'
            source = sqlite3.connect(source_path)
            try:
                source.execute('PRAGMA journal_mode=WAL')
                source.execute('CREATE TABLE plans (name TEXT, price INTEGER)')
                source.execute("INSERT INTO plans VALUES ('Test plan', 20)")
                source.commit()
                # A decoy must never be considered.
                (Path(directory) / 'db.sqlite3').write_bytes(b'not the configured database')
                archive, extension, _ = create_database_backup({
                    'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(source_path)})
                with archive:
                    snapshot_path = Path(directory) / 'restored.sqlite3'
                    snapshot_path.write_bytes(archive.read())
                restored = sqlite3.connect(snapshot_path)
                try:
                    self.assertEqual(restored.execute('SELECT * FROM plans').fetchall(), [('Test plan', 20)])
                    self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone(), ('ok',))
                finally:
                    restored.close()
                self.assertEqual(extension, 'sqlite3')
                self.assertEqual(source.execute('SELECT COUNT(*) FROM plans').fetchone(), (1,))
            finally:
                source.close()

    def test_missing_or_in_memory_sqlite_fails_without_creating_source(self):
        with tempfile.TemporaryDirectory(prefix='iconnect-backup-test-') as directory:
            missing = Path(directory) / 'missing.sqlite3'
            for name in (str(missing), ':memory:'):
                with self.assertRaises(DatabaseBackupError):
                    create_database_backup({'ENGINE': 'django.db.backends.sqlite3', 'NAME': name})
            self.assertFalse(missing.exists())


class DatabaseBackupViewTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('backup_test', 'backup@test.invalid', 'test-only')
        self.client.force_login(self.admin)

    @patch('dashboard.database_backup.create_database_backup')
    def test_non_admin_cannot_generate_or_download_backup(self, create):
        self.client.logout()
        self.assertEqual(self.client.get('/iconnect-ops/settings/backup/').status_code, 302)
        user = get_user_model().objects.create_user('ordinary', password='test-only')
        self.client.force_login(user)
        self.assertEqual(self.client.get('/iconnect-ops/settings/backup/').status_code, 302)
        create.assert_not_called()

    @patch('dashboard.database_backup.create_database_backup')
    def test_admin_streams_private_dump_and_closes_file(self, create):
        archive = io.BytesIO(b'PGDMPtest archive')
        create.return_value = archive, 'dump', 'application/octet-stream'
        response = self.client.get('/iconnect-ops/settings/backup/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertIn('.dump', response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(b''.join(response.streaming_content), b'PGDMPtest archive')
        response.close()
        self.assertTrue(archive.closed)

    @patch('dashboard.database_backup.create_database_backup', side_effect=DatabaseBackupError('PostgreSQL backup failed.'))
    def test_failure_is_not_a_successful_download(self, create):
        response = self.client.get('/iconnect-ops/settings/backup/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('Content-Disposition', response)
        self.assertContains(response, 'PostgreSQL backup failed.', status_code=503)
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_postgres_label_uses_the_active_engine(self):
        from django.conf import settings
        with patch.dict(settings.DATABASES['default'], ENGINE='django.db.backends.postgresql'):
            response = self.client.get('/iconnect-ops/settings/')
        self.assertContains(response, 'Download PostgreSQL Backup')
        self.assertContains(response, 'Export PostgreSQL Database')
        self.assertContains(response, 'pg_restore')
        self.assertContains(response, 'not encrypted')
        self.assertNotContains(response, 'Download db.sqlite3')
        self.assertNotContains(response, 'Export Complete SQLite Database')

    def test_development_label_does_not_claim_postgresql(self):
        response = self.client.get('/iconnect-ops/settings/')
        self.assertContains(response, 'Download Database Backup')
        self.assertNotContains(response, 'Download PostgreSQL Backup')


@skipUnless(os.environ.get('ICONNECT_TEST_POSTGRES_BIN'), 'Requires explicitly isolated PostgreSQL test binaries')
class PostgreSQLBackupRestoreTests(SimpleTestCase):
    def test_native_dump_restores_schema_and_records_in_disposable_cluster(self):
        # Never connects to an existing/local/production PostgreSQL service.
        binaries = Path(os.environ['ICONNECT_TEST_POSTGRES_BIN'])
        with tempfile.TemporaryDirectory(prefix='iconnect-pg-restore-test-') as directory:
            data = Path(directory) / 'cluster'
            log = Path(directory) / 'postgres.log'
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
            env['PATH'] = str(binaries) + os.pathsep + env.get('PATH', '')
            env.update(PGHOST='127.0.0.1', PGPORT=str(port), PGUSER='backup_test')

            def cli(name, *args):
                if name == 'pg_ctl':
                    # Windows postgres children can retain captured pipe handles
                    # after pg_ctl exits; route lifecycle output to no pipes.
                    return subprocess.run([str(binaries / name), *args], env=env,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          timeout=40, check=True)
                return subprocess.run([str(binaries / name), *args], env=env,
                                      capture_output=True, timeout=40, check=True)

            cli('initdb', '-D', str(data), '-U', 'backup_test', '-A', 'trust', '--encoding=UTF8', '--no-locale')
            started = False
            try:
                cli('pg_ctl', '-D', str(data), '-l', str(log), '-o', f'-h 127.0.0.1 -p {port}', '-w', 'start')
                started = True
                cli('createdb', 'backup_source')
                cli('createdb', 'backup_restore')
                sql = "CREATE TABLE demo_plans (id integer PRIMARY KEY, name text, price integer); INSERT INTO demo_plans VALUES (1, 'Test plan', 20);"
                cli('psql', '-d', 'backup_source', '-v', 'ON_ERROR_STOP=1', '-c', sql)
                config = {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'backup_source',
                          'HOST': '127.0.0.1', 'PORT': str(port), 'USER': 'backup_test', 'PASSWORD': ''}
                with patch.dict(os.environ, env, clear=True):
                    archive, extension, _ = create_database_backup(config)
                archive_path = Path(directory) / 'test.dump'
                with archive:
                    archive_path.write_bytes(archive.read())
                self.assertEqual(extension, 'dump')
                cli('pg_restore', '--list', str(archive_path))
                cli('pg_restore', '--exit-on-error', '--no-owner', '-d', 'backup_restore', str(archive_path))
                result = cli('psql', '-d', 'backup_restore', '-At', '-c', 'SELECT id, name, price FROM demo_plans;')
                self.assertEqual(result.stdout.strip(), b'1|Test plan|20')
                result = cli('psql', '-d', 'backup_source', '-At', '-c', 'SELECT count(*) FROM demo_plans;')
                self.assertEqual(result.stdout.strip(), b'1')
            finally:
                if started:
                    cli('pg_ctl', '-D', str(data), '-m', 'fast', '-w', 'stop')
