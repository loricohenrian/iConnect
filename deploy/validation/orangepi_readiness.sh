#!/usr/bin/env bash
set -u

PROJECT_ROOT="${PROJECT_ROOT:-/opt/iconnect/pisowifi}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"

PASS_COUNT=0
FAIL_COUNT=0

pass() {
    echo "[PASS] $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
    echo "[FAIL] $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

check_command() {
    local cmd="$1"
    if command -v "$cmd" >/dev/null 2>&1; then
        pass "command available: $cmd"
    else
        fail "missing command: $cmd"
    fi
}

check_service_enabled_and_active() {
    local svc="$1"
    if systemctl is-enabled "$svc" >/dev/null 2>&1; then
        pass "service enabled: $svc"
    else
        fail "service not enabled: $svc"
    fi

    if systemctl is-active "$svc" >/dev/null 2>&1; then
        pass "service active: $svc"
    else
        fail "service not active: $svc"
    fi
}

check_http_json() {
    local url="$1"
    if curl -fsS "$url" >/dev/null 2>&1; then
        pass "HTTP reachable: $url"
    else
        fail "HTTP unreachable: $url"
    fi
}

echo "=== iConnect Orange Pi Readiness Check ==="
echo "Project root: $PROJECT_ROOT"

echo
echo "-- Command checks --"
check_command systemctl
check_command iptables
check_command curl
check_command redis-cli

if [ ! -x "$PYTHON_BIN" ]; then
    fail "python executable not found: $PYTHON_BIN"
else
    pass "python executable found: $PYTHON_BIN"
fi

echo
echo "-- Service checks --"
check_service_enabled_and_active pisowifi.service
check_service_enabled_and_active coindetector.service
check_service_enabled_and_active celery-worker.service
check_service_enabled_and_active celery-beat.service
check_service_enabled_and_active nginx.service
check_service_enabled_and_active redis-server.service

echo
echo "-- Network / firewall checks --"
if iptables -S FORWARD 2>/dev/null | grep -q "^-P FORWARD DROP$"; then
    pass "FORWARD default policy is DROP"
else
    fail "FORWARD default policy is not DROP"
fi

if [ -S "/run/gunicorn/pisowifi.sock" ]; then
    pass "gunicorn socket exists: /run/gunicorn/pisowifi.sock"
else
    fail "gunicorn socket missing: /run/gunicorn/pisowifi.sock"
fi

check_http_json "http://127.0.0.1/api/plans/"
check_http_json "http://127.0.0.1/api/signal-strength/"

if redis-cli ping 2>/dev/null | grep -q "PONG"; then
    pass "Redis responds with PONG"
else
    fail "Redis ping failed"
fi

echo
echo "-- Django checks --"
if [ -x "$PYTHON_BIN" ]; then
    if "$PYTHON_BIN" "$PROJECT_ROOT/manage.py" check >/dev/null 2>&1; then
        pass "Django system check"
    else
        fail "Django system check failed"
    fi

    if "$PYTHON_BIN" -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','pisowifi.settings'); import django; django.setup(); from reports.tasks import generate_and_deliver_daily_report as f; r=f(); import sys; sys.exit(0 if r.get('status')=='success' else 1)" >/dev/null 2>&1; then
        pass "Report generation task"
    else
        fail "Report generation task failed"
    fi
fi

echo
echo "-- Manual hardware checks required --"
echo "[TODO] Insert a real coin and verify /api/coin-inserted path and queue attribution in logs."
echo "[TODO] Start a paid session from captive portal and verify internet access is granted."
echo "[TODO] Let a short session expire and verify access is revoked."
echo "[TODO] Reboot device and re-run this script to confirm persistence."

echo
echo "=== Summary ==="
echo "PASS: $PASS_COUNT"
echo "FAIL: $FAIL_COUNT"

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi

exit 0
