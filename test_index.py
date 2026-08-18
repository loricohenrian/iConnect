import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pisowifi.settings")
django.setup()
from django.test import Client
c = Client()
try:
    response = c.get("/")
    print("STATUS:", response.status_code)
    if response.status_code == 500:
        print("Template rendering error?")
except Exception as e:
    import traceback
    traceback.print_exc()
