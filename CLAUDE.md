# CLAUDE.md — iConnect Project Context & Instructions

## Project Overview

**Project Name:** iConnect — School-Based Coin-Operated WiFi System with Real-Time Monitoring and Business Analytics Dashboard

**Type:** Capstone Project — Information Technology (Business Analytics Specialization)

**Purpose:** A coin-operated WiFi system deployed in a school building that allows students to purchase internet access using Philippine coins (₱1, ₱5, ₱10, ₱20). The system includes a user-facing captive portal and a comprehensive admin dashboard with business analytics.

**Status:** In development

---

## Tech Stack

| Layer               | Technology                                              |
| ------------------- | ------------------------------------------------------- |
| Hardware controller | ALLAN H3 1GB Single Board Computer (Orange Pi H3 based) |
| Operating system    | Armbian Linux (Debian-based)                            |
| Backend framework   | Django 4.x + Django REST Framework                      |
| Database            | PostgreSQL                                              |
| Web server          | Nginx + Gunicorn                                        |
| Coin detection      | Python 3 + OPi.GPIO library                             |
| Task scheduling     | Celery + Redis                                          |
| Analytics           | Microsoft Power BI (connects to PostgreSQL)             |
| Report generation   | ReportLab (PDF)                                         |
| Frontend            | HTML + CSS + Bootstrap + JavaScript + Chart.js          |
| Version control     | Git + GitHub                                            |

---

## Hardware Setup

```
ALLAN 1239A Pro Max Coin Acceptor
        ↓ (electrical pulses via GPIO)
PisoWiFi Custom Board 5V
(handles: 12V→5V conversion, relay, fan, buzzer)
        ↓ (dupont wires to GPIO pins)
ALLAN H3 1GB Board
(runs: Armbian, Django, PostgreSQL, Python GPIO script)
        ↓ (LAN cable via RJ45)
Converge Fiber Router/ONT
(broadcasts: 2.4GHz + 5GHz WiFi)
        ↓ (WiFi)
Student devices
```

**Power source:** Solar panel (50W) → Charge controller (10A) → 12V 20Ah AGM battery → DC 12V directly to custom board (no inverter needed — all devices run on DC)

**Enclosure:** ALLAN Metal Piso WiFi Box with double lock and coin collection drawer

---

## Project Structure

```
pisowifi/
â”œâ”€â”€ pisowifi/              # Project settings
â”‚   â”œâ”€â”€ settings.py
â”‚   â”œâ”€â”€ urls.py
â”‚   â””â”€â”€ wsgi.py
â”‚
â”œâ”€â”€ portal/                # USER SIDE — Captive Portal
â”‚   â”œâ”€â”€ models.py
â”‚   â”œâ”€â”€ views.py
â”‚   â”œâ”€â”€ urls.py
â”‚   â””â”€â”€ templates/portal/
â”‚       â”œâ”€â”€ index.html         # Plan selection
â”‚       â”œâ”€â”€ session.html       # Session timer page
â”‚       â”œâ”€â”€ history.html       # Usage history
â”‚       â””â”€â”€ manual.html        # User guide/FAQ
â”‚
â”œâ”€â”€ dashboard/             # ADMIN SIDE — Dashboard
â”‚   â”œâ”€â”€ models.py
â”‚   â”œâ”€â”€ views.py
â”‚   â”œâ”€â”€ urls.py
â”‚   â””â”€â”€ templates/dashboard/
â”‚       â”œâ”€â”€ overview.html      # Live overview
â”‚       â”œâ”€â”€ revenue.html       # Revenue monitoring
â”‚       â”œâ”€â”€ sessions.html      # Session logs
â”‚       â”œâ”€â”€ reports.html       # Exportable reports
â”‚       â”œâ”€â”€ heatmap.html       # Peak hours heatmap
â”‚       â”œâ”€â”€ analytics.html     # User behavior analytics
â”‚       â”œâ”€â”€ roi.html           # ROI tracker
â”‚       â””â”€â”€ announcements.html # Announcement management
â”‚
â”œâ”€â”€ analytics/             # Business Analytics Layer
â”‚   â”œâ”€â”€ models.py
â”‚   â”œâ”€â”€ views.py
â”‚   â””â”€â”€ templates/analytics/
â”‚       â”œâ”€â”€ predictive.html    # Revenue forecasting
â”‚       â””â”€â”€ pricing.html       # Pricing recommendations
â”‚
â”œâ”€â”€ sessions/              # Session Management
â”‚   â”œâ”€â”€ models.py          # Session, CoinEvent, Plan, WhitelistedDevice
â”‚   â”œâ”€â”€ views.py
â”‚   â””â”€â”€ urls.py
â”‚
â”œâ”€â”€ gpio/                  # Hardware Interface
â”‚   â””â”€â”€ coin_detector.py   # Python GPIO coin detection script
â”‚
â”œâ”€â”€ reports/               # Report Generation
â”‚   â””â”€â”€ generator.py       # ReportLab PDF generator
â”‚
â”œâ”€â”€ static/                # CSS, JS, images
â”œâ”€â”€ templates/             # Base templates
â”œâ”€â”€ requirements.txt
â””â”€â”€ manage.py
```

---

## Database Models

### Plan

```python
- id
- name (e.g., "₱5 Plan")
- price (e.g., 5)
- duration_minutes (e.g., 30)
- speed_limit (optional Mbps cap)
- is_active
- created_at
```

### Session

```python
- id
- mac_address
- plan (FK to Plan)
- time_in
- time_out
- duration_minutes_purchased
- remaining_minutes
- amount_paid
- status (active/expired/paused)
- voucher_code (for session extension)
- bandwidth_used_mb
- ip_address
- device_name
- created_at
```

### CoinEvent

```python
- id
- amount
- denomination
- session (FK to Session, nullable)
- timestamp
```

### WhitelistedDevice

```python
- id
- mac_address
- device_name (e.g., "Admin laptop")
- added_by
- date_added
```

### Announcement

```python
- id
- message
- is_active
- created_at
- updated_at
```

### RevenueGoal

```python
- id
- period (daily/weekly)
- target_amount
- created_at
```

### ProjectCost

```python
- id
- description
- amount
- date_added
```

### DailyRevenueSummary

```python
- id
- date
- total_revenue
- total_sessions
- avg_session_minutes
- peak_hour
- created_at
```

---

## Key API Endpoints

| Method | URL                   | Purpose                                     |
| ------ | --------------------- | ------------------------------------------- |
| POST   | /api/coin-inserted/   | Receives coin pulse data from GPIO script   |
| POST   | /api/session/start/   | Creates new session after payment           |
| POST   | /api/session/extend/  | Extends session via voucher code            |
| POST   | /api/session/end/     | Ends session when time expires              |
| GET    | /api/session/status/  | Returns remaining time for active session   |
| GET    | /api/connected-users/ | Returns list of currently connected devices |
| GET    | /api/bandwidth/       | Returns bandwidth usage per user            |
| POST   | /api/whitelist/       | Adds device to whitelist                    |
| GET    | /api/signal-strength/ | Returns RSSI for connected devices          |
| GET    | /api/announcements/   | Returns active announcements                |
| POST   | /api/announcements/   | Creates new announcement                    |

---

## Coin Detection Logic

The ALLAN 1239A coin acceptor sends electrical pulses to GPIO pin on ALLAN H3:

- ₱1 = 1 pulse
- ₱5 = 5 pulses
- ₱10 = 10 pulses
- ₱20 = 20 pulses

Python GPIO script (`gpio/coin_detector.py`) counts pulses, waits 500ms timeout to confirm insertion complete, then sends amount to Django via HTTP POST to `localhost:8000/api/coin-inserted/`.

**GPIO Pin:** Configurable from admin dashboard (default: GPIO pin 7)

---

## Internet Access Control

Uses Linux `iptables` firewall on ALLAN H3:

```bash
# Default — block all devices
iptables -P FORWARD DROP

# Whitelist device (permanent)
iptables -A FORWARD -m mac --mac-source [MAC] -j ACCEPT

# Allow paid session (temporary)
iptables -A FORWARD -m mac --mac-source [MAC] -j ACCEPT

# Remove access when time expires
iptables -D FORWARD -m mac --mac-source [MAC] -j ACCEPT
```

Django calls these commands via Python `subprocess` library.

---

## Session Extension — No OLED (Portal Queue Top-Up)

1. Student opens captive portal session page and taps "Extend Session"
2. Django creates a short-lived extension request bound to device MAC + IP
3. System places the request in a queue (single coin slot means one active request window at a time)
4. When request is at the front of the queue, portal shows "Insert coins now" (30-60 second window)
5. Student inserts coins into the machine
6. GPIO script detects pulses and sends amount to Django via `/api/coin-inserted/`
7. Django assigns the coin event to the active queued request (no OLED code needed)
8. Backend extends that device session immediately (or accumulates credit until plan threshold is met)
9. Portal polls status and shows success/failure message; if timeout occurs, request expires and queue advances

---

## Business Analytics Framework

### Descriptive Analytics

- Daily/weekly/monthly/yearly revenue totals
- Session count and average duration
- Most popular plans
- Total bandwidth consumed

### Diagnostic Analytics

- Peak hours heatmap (7 days × 24 hours grid)
- Low revenue day identification
- User behavior patterns
- Bandwidth consumption patterns

### Prescriptive Analytics

- WiFi band recommendation (2.4G vs 5G) based on RSSI signal strength
- Pricing recommendations based on usage data
- Revenue goal progress tracking
- ROI breakeven forecast

---

## ROI Tracking

Admin enters total project cost on setup. System tracks:

- Cumulative revenue vs total project cost
- Daily average revenue
- Projected breakeven date
- Percentage of ROI recovered

**Target:** Full ROI recovery within 3 months of deployment

---

## Solar Power Savings Monitoring

System calculates estimated electricity savings:

- Known device power consumption (ALLAN H3: ~5W, router: ~10W, coinslot: ~3W)
- Total system consumption: ~18W
- Multiplied by current electricity rate (~₱10–12/kWh)
- Displayed as monthly/cumulative savings in admin dashboard

---

## Whitelisted Devices

Family members, admin devices, and school staff devices bypass the captive portal and coin payment system. Managed via Django admin dashboard. MAC address is the identifier.

---

## Deployment Notes

- System runs locally on ALLAN H3 — no external hosting required for basic operation
- Admin dashboard accessible from any device on same WiFi network
- URL format: `http://[ALLAN H3 IP ADDRESS]/admin`
- Captive portal auto-redirects via Nginx when student opens any browser
- All services run as systemd services and start automatically on boot:
  - `pisowifi.service` — Django + Gunicorn
  - `nginx.service` — Web server
  - `coindetector.service` — GPIO coin detection - `postgresql.service` — Database - `redis-server.service` — Redis broker/cache - `celery-worker.service` — Celery worker process - `celery-beat.service` — Celery scheduler process

---

## Development Notes

- Build and test all features on laptop first using `localhost:8000`
- Simulate coin insertions during development by calling `/api/coin-inserted/` directly via Postman or browser
- Use `python manage.py runserver` for local development
- Use Gunicorn + Nginx for production deployment on ALLAN H3
- Always push to GitHub before deploying to ALLAN H3
- Deploy to ALLAN H3 via SSH + `git pull`

---

## Important Constraints

- Philippine coins only (₱1, ₱5, ₱10, ₱20) — no GCash or digital payments
- System is local network only unless hosted on cloud (Railway/Render)
- University does not allow plugging into electrical outlets — solar power is mandatory
- Revenue from system goes entirely toward paying monthly Converge ISP bill
- Single coinslot shared by all users — voucher system required for session extension

---

## Coding Standards

- Follow Django best practices — fat models, thin views
- All API endpoints use Django REST Framework
- Use Django ORM — no raw SQL
- All templates use Bootstrap for responsive design
- JavaScript should be vanilla JS or minimal jQuery — no heavy frameworks
- All monetary values stored in Philippine Peso (₱) as integers (no decimals)
- MAC addresses stored in uppercase with colons: `AA:BB:CC:DD:EE:FF`
- All timestamps stored in Asia/Manila timezone
- Comments in English
- Variable names in English
- Always validate coin amounts before creating sessions
