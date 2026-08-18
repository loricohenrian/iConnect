import os
import django
import json
from django.test import RequestFactory
from django.utils import timezone
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pisowifi.settings")
django.setup()

from sessions_app.views import session_join_group
from sessions_app.models import Plan, Session, SessionGroup

# Cleanup
Session.objects.all().delete()
SessionGroup.objects.all().delete()

plan, _ = Plan.objects.get_or_create(name="Test Group Plan", price=10, duration_minutes=60, speed_limit=5, is_active=True)

# Create host session group and session
now = timezone.now()
from datetime import timedelta
group = SessionGroup.objects.create(
    group_code="TESTCODE",
    max_devices=5,
    total_price=50,
    duration_minutes=60,
    time_in=now,
    time_out=now + timedelta(minutes=60),
    status="active"
)

Session.objects.create(
    mac_address="00:11:22:33:44:55",
    plan=plan,
    session_group=group,
    time_in=now,
    time_out=now + timedelta(minutes=60),
    duration_minutes_purchased=60,
    amount_paid=50,
    ip_address="192.168.1.100",
    status="active"
)

# Test payload
data = {
    "mac_address": "00:11:22:33:44:66", # New device
    "group_code": "TESTCODE"
}

factory = RequestFactory()
request = factory.post('/api/session/join-group/', data=json.dumps(data), content_type='application/json')
request.META["REMOTE_ADDR"] = "192.168.1.101"

with patch("django.core.cache.cache.get", return_value=0):
    with patch("django.core.cache.cache.set"):
        with patch("django.core.cache.cache.delete"):
            with patch("sessions_app.iptables.allow_device") as mock_allow:
                with patch("sessions_app.views._ensure_firewall_ready_for_session_start", return_value=True):
                    try:
                        response = session_join_group(request)
                        print("Status:", response.status_code)
                        print("Data:", response.data)
                    except Exception as e:
                        import traceback
                        traceback.print_exc()

