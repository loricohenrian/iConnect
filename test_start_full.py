import os
import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pisowifi.settings')
django.setup()

from rest_framework.test import APIRequestFactory
from sessions_app.views import session_start
from dashboard.models import SystemSettings
from sessions_app.models import Plan

SystemSettings.objects.get_or_create(id=1)
plan, _ = Plan.objects.get_or_create(id=1, defaults={"name": "Test", "price": 10, "duration_minutes": 60})

factory = APIRequestFactory()
request = factory.post('/api/session/start/', {
    "mac_address": "AA:BB:CC:DD:EE:FF",
    "plan_id": plan.id,
    "is_group_pass": True,
    "group_pass_devices": 2
}, format='json')

response = session_start(request)
print(response.status_code)
print(response.data)

