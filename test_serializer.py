import os
import django
import json

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pisowifi.settings")
django.setup()

from sessions_app.serializers import SessionStartSerializer

data = {
    "mac_address": "00:11:22:33:44:55",
    "plan_id": 1,
    "is_group_pass": True,
    "group_pass_devices": None
}
serializer = SessionStartSerializer(data=data)
if serializer.is_valid():
    print("Valid!")
    print(serializer.validated_data)
else:
    print("Invalid!")
    print(serializer.errors)

