import os
import django
import json
from django.test import RequestFactory
from django.utils import timezone
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pisowifi.settings")
django.setup()

from sessions_app.views import session_start
from sessions_app.models import Plan, Session, CoinInsertRequest, CoinEvent

# Cleanup old sessions and coins
Session.objects.all().delete()
CoinInsertRequest.objects.all().delete()
CoinEvent.objects.all().delete()

# Make sure there is a plan
plan, _ = Plan.objects.get_or_create(name="Test Group Plan", price=10, duration_minutes=60, speed_limit=5, is_active=True)

CoinEvent.objects.create(mac_address="00:11:22:33:44:55", amount=50, denomination=10)

CoinInsertRequest.objects.create(
    mac_address="00:11:22:33:44:55",
    ip_address="192.168.1.100",
    purpose=CoinInsertRequest.PURPOSE_START,
    plan=plan,
    is_group_pass=True,
    group_pass_devices=5,
    expected_amount=50,
    credited_amount=50,
    status=CoinInsertRequest.STATUS_COMPLETED,
    completed_at=timezone.now()
)

# Test payload
data = {
    "mac_address": "00:11:22:33:44:55",
    "plan_id": plan.id,
    "is_group_pass": True,
    "group_pass_devices": 5,
    "device_name": "TestDevice"
}

factory = RequestFactory()
request = factory.post('/api/session/start/', data=json.dumps(data), content_type='application/json')
request.META["REMOTE_ADDR"] = "192.168.1.100"

with patch("sessions_app.iptables.allow_device") as mock_allow:
    with patch("sessions_app.views._ensure_firewall_ready_for_session_start", return_value=True):
        try:
            response = session_start(request)
            print("Status:", response.status_code)
            print("Data:", response.data)
        except Exception as e:
            import traceback
            traceback.print_exc()
