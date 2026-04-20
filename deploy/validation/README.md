# Orange Pi Readiness Validation

Use this script on the Orange Pi to validate service health and deployment readiness.

## Run

```bash
cd /opt/iconnect/pisowifi
chmod +x deploy/validation/orangepi_readiness.sh
sudo PROJECT_ROOT=/opt/iconnect/pisowifi deploy/validation/orangepi_readiness.sh
```

## What it checks

- Required commands (`systemctl`, `iptables`, `curl`, `redis-cli`)
- Service enablement and active state:
  - `pisowifi.service`
  - `coindetector.service`
  - `celery-worker.service`
  - `celery-beat.service`
  - `nginx.service`
  - `redis-server.service`
- Firewall default policy (`FORWARD DROP`)
- Gunicorn socket presence
- Local API reachability (`/api/plans/`, `/api/signal-strength/`)
- Redis `PONG`
- `python manage.py check`
- Daily report generation task

## Manual checks still required

- Insert a real coin and verify queue attribution path in logs
- Start a real paid session and verify internet is granted
- Let a short session expire and verify internet is revoked
- Reboot Orange Pi and re-run the script to confirm persistence
