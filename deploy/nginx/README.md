# Nginx Site Config (Orange Pi / Armbian)

This folder provides an Nginx virtual host for iConnect with static/media routing.

## 1) Install Nginx

```bash
sudo apt update
sudo apt install -y nginx
```

## 2) Copy and enable site

```bash
cd /opt/iconnect/pisowifi
sudo cp deploy/nginx/iconnect.conf /etc/nginx/sites-available/iconnect.conf
sudo ln -sf /etc/nginx/sites-available/iconnect.conf /etc/nginx/sites-enabled/iconnect.conf
sudo rm -f /etc/nginx/sites-enabled/default
```

## 3) Validate and reload

```bash
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

## 4) Static/media prerequisites

Run collectstatic so `/static/` is served from `staticfiles/`:

```bash
cd /opt/iconnect/pisowifi
source .venv/bin/activate
python manage.py collectstatic --noinput
```

Nginx static/media paths expected by `iconnect.conf`:

- `/opt/iconnect/pisowifi/staticfiles/`
- `/opt/iconnect/pisowifi/media/`
