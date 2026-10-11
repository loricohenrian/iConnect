"""Native backups of the configured database, never a guessed database file."""
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time


class DatabaseBackupError(Exception):
    """Safe, user-facing failure without connection credentials or tool output."""


def _pgpass_value(value):
    value = str(value or '')
    if '\n' in value or '\r' in value:
        raise DatabaseBackupError('Database credentials cannot be used for this backup.')
    return value.replace('\\', '\\\\').replace(':', '\\:')


def _postgres_backup(config, output):
    if not config.get('NAME'):
        raise DatabaseBackupError('The PostgreSQL database name is not configured.')
    # Do not inherit a different PGDATABASE, PGSERVICE, or password from the
    # service environment. Credentials never appear in command-line arguments.
    env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    for key, variable in (('NAME', 'PGDATABASE'), ('HOST', 'PGHOST'),
                          ('PORT', 'PGPORT'), ('USER', 'PGUSER')):
        if config.get(key):
            env[variable] = str(config[key])
    env['PGCONNECT_TIMEOUT'] = '10'
    for key, variable in (('sslmode', 'PGSSLMODE'), ('sslrootcert', 'PGSSLROOTCERT'),
                          ('sslcert', 'PGSSLCERT'), ('sslkey', 'PGSSLKEY')):
        if config.get('OPTIONS', {}).get(key):
            env[variable] = str(config['OPTIONS'][key])
    with tempfile.TemporaryDirectory(prefix='iconnect-pgpass-') as credential_dir:
        passfile = Path(credential_dir) / 'pgpass'
        # Only this subprocess receives the private, short-lived password file.
        entry = ':'.join(('*', '*', _pgpass_value(config['NAME']),
                          _pgpass_value(config.get('USER') or '*'),
                          _pgpass_value(config.get('PASSWORD'))))
        fd = os.open(passfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(entry + '\n')
        env['PGPASSFILE'] = str(passfile)
        try:
            result = subprocess.run(
                ['pg_dump', '--format=custom', '--no-password', '--lock-wait-timeout=10s'],
                stdout=output, stderr=subprocess.PIPE, env=env, timeout=40,
                check=False,
            )
        except FileNotFoundError:
            raise DatabaseBackupError('PostgreSQL backup tool (pg_dump) is unavailable on the server.') from None
        except subprocess.TimeoutExpired:
            raise DatabaseBackupError('PostgreSQL backup timed out. Please try again later.') from None
        if result.returncode != 0:
            # stderr may contain connection details; do not expose or log it.
            raise DatabaseBackupError('PostgreSQL backup failed. Check server backup access and pg_dump compatibility.')
    output.seek(0)
    if output.read(5) != b'PGDMP':
        raise DatabaseBackupError('PostgreSQL did not produce a valid backup archive.')


def _sqlite_backup(config, output):
    name = config.get('NAME')
    if not name or str(name) == ':memory:' or str(name).startswith('file:'):
        raise DatabaseBackupError('A file-backed SQLite database is required for this development backup.')
    source = Path(name).resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise DatabaseBackupError('The configured SQLite database file is unavailable.')
    # Read only the exact configured source, including its committed WAL data.
    deadline = time.monotonic() + 40

    def check_deadline(*args):
        if time.monotonic() >= deadline:
            raise DatabaseBackupError('SQLite backup timed out. Please try again later.')

    with tempfile.TemporaryDirectory(prefix='iconnect-sqlite-backup-') as backup_dir:
        destination = Path(backup_dir) / 'snapshot.sqlite3'
        src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            dest = sqlite3.connect(destination)
            try:
                src.backup(dest, pages=256, progress=check_deadline)
            finally:
                dest.close()
        finally:
            src.close()
        with destination.open('rb') as handle:
            shutil.copyfileobj(handle, output)
    output.seek(0)
    if output.read(16) != b'SQLite format 3\x00':
        raise DatabaseBackupError('SQLite did not produce a valid backup snapshot.')


def create_database_backup(config):
    """Return (temporary binary file, extension, content type); caller closes it."""
    output = tempfile.TemporaryFile(mode='w+b')
    try:
        engine = config.get('ENGINE', '')
        if engine == 'django.db.backends.postgresql':
            _postgres_backup(config, output)
            extension, content_type = 'dump', 'application/octet-stream'
        elif engine == 'django.db.backends.sqlite3':
            _sqlite_backup(config, output)
            extension, content_type = 'sqlite3', 'application/x-sqlite3'
        else:
            raise DatabaseBackupError('The configured database does not support this backup download.')
        output.seek(0)
        return output, extension, content_type
    except DatabaseBackupError:
        output.close()
        raise
    except Exception:
        output.close()
        raise DatabaseBackupError('Database backup could not be generated. Please check server backup access.') from None
