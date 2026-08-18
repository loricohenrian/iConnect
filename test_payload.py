import os
import django
import json

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pisowifi.settings")
django.setup()

from sessions_app.views import _coin_request_payload
from sessions_app.models import CoinInsertRequest

req = CoinInsertRequest.objects.last()
print("ID:", req.id)
print("is_group_pass:", req.is_group_pass)
print("group_pass_devices:", req.group_pass_devices)
print("payload:", json.dumps(_coin_request_payload(req)))

