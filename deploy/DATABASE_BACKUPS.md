# Database downloads

Settings uses the configured Django database engine and connection, never a
nearby `db.sqlite3` file. PostgreSQL downloads are native custom-format
`pg_dump` archives (`.dump`) containing the single database's schema and data.
File-backed SQLite development setups use the SQLite online backup API for the
exact configured file, including committed WAL records. Unsupported databases,
missing tools, failed exports, and timeouts return an explicit error, not a JSON
or unrelated SQLite fallback.

Only authenticated dashboard staff can download backups. Downloads are marked
private/no-store. The export is read-only but consumes database, CPU, and disk
resources; avoid repeated downloads during peak usage. PostgreSQL exports have
a 10-second connection/lock wait and a 40-second process timeout, below the
repository's 60-second web-worker timeout. A larger database may require a
separate maintenance/export workflow rather than increasing worker timeouts
incidentally.

The server must have a compatible `pg_dump` executable on its service PATH,
and the configured database user must have access to all exported objects.
Database passwords use a private temporary pgpass file, not command-line
arguments or error messages. Temporary archives close after download or error.

## Protect and verify the backup

These files are **not encrypted**. They contain customer records, password
hashes, and stored configuration credentials such as the Telegram bot token.
Keep them private in access-controlled/encrypted off-device storage. Do not
upload them to a public repository, chat, or shared presentation folder.
Existing HTTPS/private network transport remains important; this change does
not add HTTPS to the application.

The archive is not a complete Orange Pi image: application files, uploaded
media, `.env`, operating-system files, and cluster-wide PostgreSQL roles and
tablespaces require separate protected backups. It is a manual download,
not an automatic off-device backup schedule or point-in-time recovery setup.

Use a compatible PostgreSQL installation to inspect it with:

```sh
pg_restore --list iconnect_backup_TIMESTAMP.dump
```

Verify a restore into an **explicitly separate empty test database**, using
`pg_restore --exit-on-error --no-owner --no-acl --dbname=RESTORE_TEST_DATABASE
iconnect_backup_TIMESTAMP.dump`. Set up the destination role/permissions
separately. Never use the live customer database as the destination, and do not
add `--clean` to a production restore as a test. Stop if the destination has
not been independently verified. Inspect restored schema, row counts, and
representative records privately before declaring recovery verified.

Automated tests include a real dump/restore using only an explicitly disposable
local cluster when `ICONNECT_TEST_POSTGRES_BIN` points to test binaries. They
never restore into an existing server or customer database. Pi-based export
and disaster-recovery verification remain separate deployment checks.
