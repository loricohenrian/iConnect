import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pisowifi.settings')
django.setup()

from sessions_app.models import Session
from sessions_app import iptables

print("Fixing throttled sessions...")
active_sessions = Session.objects.filter(status='active')
for session in active_sessions:
    mac = session.mac_address
    print(f"Re-applying bandwidth for {mac}...")
    dl_kbps = int(session.plan.speed_limit * 1024) if session.plan and session.plan.speed_limit else None
    ul_kbps = int(session.plan.speed_limit_upload * 1024) if session.plan and session.plan.speed_limit_upload else dl_kbps
    iptables.allow_device(mac, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
print("Done!")
