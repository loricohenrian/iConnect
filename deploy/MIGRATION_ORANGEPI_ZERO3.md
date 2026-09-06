# Migrating iConnect PisoWiFi to Orange Pi Zero 3

This guide provides step-by-step instructions to migrate your existing iConnect PisoWiFi setup (from Orange Pi One or any device) to a fresh **Orange Pi Zero 3 (Allwinner H618)** with zero downtime and no manual configuration headaches.

All systemd services, udev hotplug rules, dnsmasq dynamic binding, and Nginx configurations are automated via `deploy/setup_orangepi.sh`.

---

## Phase 1: Backup Existing Machine (Orange Pi One)

Run these commands on your current machine to save your database and settings:

### 1. Backup PostgreSQL database:
```bash
sudo -u postgres pg_dump iConnect > /opt/iconnect/backup_iconnect.sql
```

### 2. Copy `.env` and database backup to your PC / USB:
From your PC (PowerShell / Terminal):
```bash
scp root@<ORANGE_PI_ONE_IP>:/opt/iconnect/backup_iconnect.sql .
scp root@<ORANGE_PI_ONE_IP>:/opt/iconnect/pisowifi/.env .
```

---

## Phase 2: Prepare Orange Pi Zero 3 (OS & Network)

### 1. Install OS:
- Download the official **Armbian (Debian Bookworm or Ubuntu Jammy)** image for **Orange Pi Zero 3**.
- Flash to a high-speed MicroSD card (SanDisk Extreme / Ultra recommended) using BalenaEtcher or Rufus.
- Insert card, connect WAN Ethernet (`end0`) and USB-to-LAN adapter (`usblan0`), and power on.
- Complete initial Armbian setup (set root password, timezone `Asia/Manila`).

### 2. Configure Static IP on `usblan0` (10.10.10.1):
Run on Orange Pi Zero 3:
```bash
sudo nmcli con add type ethernet con-name usblan0 ifname usblan0 ip4 10.10.10.1/24
sudo nmcli con up usblan0
```
Verify with `ip -4 addr show usblan0` that it shows `inet 10.10.10.1/24`.

---

## Phase 3: Clone Repository & Run Automated Setup

### 1. Install Git and PostgreSQL:
```bash
sudo apt update
sudo apt install -y git postgresql postgresql-contrib
```

### 2. Clone iConnect repository:
```bash
sudo mkdir -p /opt/iconnect
sudo git clone https://github.com/loricohenrian/iConnect.git /opt/iconnect/pisowifi
cd /opt/iconnect/pisowifi
```

### 3. Run the Automated Setup Script:
```bash
sudo bash deploy/setup_orangepi.sh
```
This single script automatically:
- Installs `nginx`, `redis-server`, `dnsmasq`, `iptables`, `conntrack`.
- Configures `dnsmasq` with `bind-dynamic` (fixes the USB adapter boot race condition permanently).
- Sets up systemd drop-in `wait-for-usblan0.conf`.
- Installs `udev` rule `99-usblan0.rules` for auto-reloading if USB is replugged.
- Installs all 4 systemd units: `pisowifi`, `coindetector`, `celery-worker`, `celery-beat`.
- Configures and enables Nginx captive portal redirection.

---

## Phase 4: Python Environment & Database Restore

### 1. Set up Python Virtual Environment:
```bash
cd /opt/iconnect/pisowifi
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Restore `.env`:
Copy your saved `.env` file to `/opt/iconnect/pisowifi/.env`:
```bash
chmod 600 /opt/iconnect/pisowifi/.env
```

### 3. Restore Database:
Create database user and restore SQL:
```bash
# Check DATABASE_URL in .env to get user and password
sudo -u postgres psql -c "CREATE DATABASE "iConnect";"
sudo -u postgres psql -c "CREATE USER iconnect WITH PASSWORD 'your_db_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE "iConnect" TO iconnect;"
sudo -u postgres psql -c "ALTER DATABASE "iConnect" OWNER TO iconnect;"

# Restore data
sudo -u postgres psql iConnect < /path/to/backup_iconnect.sql

# Run migrations and collect static files
source .venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
```

---

## Phase 5: GPIO Wiring on Orange Pi Zero 3

The coin detector service uses pin **GPIO 3** (physical pin 5 on header) by default.
- Signal wire from coin acceptor -> GPIO Pin 3 (or configured `GPIO_PIN` in `.env`).
- 12V / GND connected to 12V power supply and common ground.
- Optional coin relay wire -> configured `COIN_RELAY_PIN`.

---

## Phase 6: Start All Services & Verify

```bash
sudo systemctl restart redis-server dnsmasq nginx
sudo systemctl restart pisowifi coindetector celery-worker celery-beat

# Run readiness validation
bash deploy/validation/orangepi_readiness.sh
```

All tests should show `[PASS]`. Connect your phone to the Wi-Fi AP, and your captive portal will load immediately!
