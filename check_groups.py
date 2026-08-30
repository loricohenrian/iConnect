import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pisowifi.settings')
django.setup()

from sessions_app.models import SessionGroup

print("\n--- LATEST 3 GROUP PASSES ---")
for g in SessionGroup.objects.order_by('-id')[:3]:
    print(f"Group: {g.group_code} | max: {g.max_devices} | redeemed: {g.redeemed_count} | created: {g.time_in.strftime('%H:%M:%S')}")
    for s in g.sessions.all():
        print(f"  - MAC: {s.mac_address} | Status: {s.status}")
print("-----------------------------\n")
