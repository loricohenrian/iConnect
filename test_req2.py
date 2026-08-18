import os
import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pisowifi.settings')
django.setup()

from rest_framework.test import APIRequestFactory
from sessions_app.views import session_start_request
from dashboard.models import SystemSettings
from sessions_app.models import Plan

SystemSettings.objects.get_or_create(id=1)
plan, _ = Plan.objects.get_or_create(id=1, defaults={"name": "Test", "price": 10, "duration_minutes": 60})

factory = APIRequestFactory()
request = factory.post('/api/session/start/request/', {
    "mac_address": "AA:BB:CC:DD:EE:11",
    "plan_id": plan.id,
    "is_group_pass": True,
    "group_pass_devices": 2,
    "group_pass_duration_minutes": 0
}, format='json')

try:
    response = session_start_request(request)
    print(response.status_code)
    print(response.data)
except Exception as e:
    import traceback
    traceback.print_exc()

