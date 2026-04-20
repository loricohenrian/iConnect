# iConnect Services (Orange Pi / Armbian)

This folder contains systemd units for Django/Gunicorn, coin detector, and Celery.

## 1) Install base packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip nginx redis-server
```

## 2) Prepare project

Assumed path:

- `/opt/iconnect/pisowifi`
- virtualenv: `/opt/iconnect/pisowifi/.venv`
- env file: `/opt/iconnect/pisowifi/.env`

If path differs, edit service files before copying.

## 3) Install Python deps and static assets

```bash
cd /opt/iconnect/pisowifi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
```

## 4) Install and enable Redis

```bash
sudo systemctl enable --now redis-server
redis-cli ping
```

Expected output: `PONG`

## 5) Install systemd unit files

```bash
cd /opt/iconnect/pisowifi
sudo cp deploy/systemd/pisowifi.service /etc/systemd/system/
sudo cp deploy/systemd/coindetector.service /etc/systemd/system/
sudo cp deploy/systemd/celery-worker.service /etc/systemd/system/
sudo cp deploy/systemd/celery-beat.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## 6) Enable application services

```bash
sudo systemctl enable --now pisowifi.service
sudo systemctl enable --now coindetector.service
sudo systemctl enable --now celery-worker.service
sudo systemctl enable --now celery-beat.service
```

## 7) Enable Nginx site

Use config in `deploy/nginx/iconnect.conf`:

```bash
sudo cp deploy/nginx/iconnect.conf /etc/nginx/sites-available/iconnect.conf
sudo ln -sf /etc/nginx/sites-available/iconnect.conf /etc/nginx/sites-enabled/iconnect.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

## 8) Verify

```bash
sudo systemctl status pisowifi.service --no-pager
sudo systemctl status coindetector.service --no-pager
sudo systemctl status celery-worker.service --no-pager
sudo systemctl status celery-beat.service --no-pager
sudo systemctl status nginx --no-pager
journalctl -u pisowifi.service -n 100 --no-pager
journalctl -u coindetector.service -n 100 --no-pager
journalctl -u celery-worker.service -n 100 --no-pager
journalctl -u celery-beat.service -n 100 --no-pager
```

## Required env vars

Ensure these exist in `.env`:

```dotenv
DATABASE_URL=postgres://<user>:<password>@127.0.0.1:5432/iConnect
REDIS_URL=redis://127.0.0.1:6379/0
CACHE_URL=redis://127.0.0.1:6379/1
```

## Notes

- `pisowifi.service` and Celery services run as `root` because this project executes `iptables` commands from app/task code.
- If you move to least-privilege later, provide a privileged firewall helper or NET_ADMIN capability.
