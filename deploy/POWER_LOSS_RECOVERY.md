# Power-loss session recovery

## Behaviour

- Active-session balances are saved to the database on timer/purchase changes and
  by the periodic expiry task (configured every 10 seconds in settings).
- After an OS reboot, database recovery runs before HTTP views, expiry/checkpoint
  tasks, disconnect checks, and ISP monitoring. Worker restarts in the same OS
  boot do not refund time or convert active sessions into power pauses.
- Recovered active sessions remain **power-paused** until the original device
  replies to a fresh MAC-verified ARP probe, or the customer/admin resumes them.
  Firewall access must succeed before automatic/customer resume starts paid time.
- Power pauses do not consume pause chances and have no overnight/multi-day
  expiry. Ordinary manual/disconnect/ISP pauses keep their existing semantics;
  reboot downtime is excluded from their pause/lifetime limits.
- Recovery state and protected balances are in the database, not Redis. Reboots,
  repeated worker starts, and cleared caches cannot refund the same outage twice.
- SQLite uses WAL with `synchronous=FULL` for durable committed checkpoints.
  A database/storage backup is still required; no software can recover from an
  unreadable/destroyed SD card or database.

An abrupt outage preserves the **last committed checkpoint**, not necessarily the
exact shutdown second. A late checkpoint can give a small amount of time back.
If Celery/Beat is down, checkpoints become stale and that margin grows; monitor
those services. PostgreSQL's normal durable commit settings must remain enabled.

Recovery is keyed to Linux `/proc/sys/kernel/random/boot_id` (the Orange Pi's OS),
not worker PID or Redis. Windows uses a development-only identifier and does not
provide production reboot detection. If the system clock is behind a saved
checkpoint, requests fail closed with HTTP 503 until time synchronizes; balances
are not expired while recovery is blocked.

## Deploy on the Orange Pi

Back up the production database first. Deploy all changed Python files and
`sessions_app/migrations/0019_session_power_recovery.py` together. Stop application
services while deploying/migrating so old code cannot overwrite new checkpoints.
Do not stop or flush Redis to perform this update.

```bash
cd /opt/iconnect/pisowifi
sudo systemctl stop celery-beat.service celery-worker.service coindetector.service pisowifi.service
# Deploy the updated source here using your normal process.
sudo .venv/bin/python manage.py migrate
sudo apt-get install -y iputils-arping
sudo systemctl start pisowifi.service celery-worker.service celery-beat.service coindetector.service
sudo systemctl is-active pisowifi.service celery-worker.service celery-beat.service
sudo journalctl -u celery-worker.service -u pisowifi.service -n 100 --no-pager
```

The first run adopts the existing `heartbeat.txt` if valid, as a one-time upgrade
bridge. Without a legacy heartbeat, it establishes a baseline from current
balances. It cannot infer credits already lost before installation and never
resurrects expired sessions (including session #727). Historical customer credits
require a separate reviewed admin adjustment.

For a controlled acceptance test, create a test session, wait for a checkpoint,
record its remaining time, then perform a **clean OS reboot**. Check that the
session returns paused at approximately the recorded balance, survives overnight
limits, and resumes only when its device returns. Do not deliberately pull power
from the production SD card to test this feature.

## Automated tests

```bash
python manage.py test sessions_app.test_power_recovery --noinput
python manage.py test sessions_app dashboard portal --noinput
python manage.py makemigrations --check --dry-run
```
