import re

with open("sessions_app/views.py", "r", encoding="utf-8") as f:
    code = f.read()

old_join = """            session = Session.objects.create(
                mac_address=mac_address,
                plan=None,
                session_group=group,
                time_in=timezone.now(),
                duration_minutes_purchased=remaining_minutes,
                amount_paid=0,
                ip_address=ip_address,
                device_name=device_name,
                status="active",
            )
            
            from dashboard.models import SystemSettings
            settings_obj = SystemSettings.get_settings()
            dl_kbps = int(settings_obj.family_pass_speed_limit * 1024)
            ul_kbps = int(settings_obj.family_pass_speed_limit_upload * 1024)
            
            if not iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps):"""

new_join = """            # Inherit plan from the group's creator session
            creator_session = group.session_set.first()
            group_plan = creator_session.plan if creator_session else None

            session = Session.objects.create(
                mac_address=mac_address,
                plan=group_plan,
                session_group=group,
                time_in=timezone.now(),
                duration_minutes_purchased=remaining_minutes,
                amount_paid=0,
                ip_address=ip_address,
                device_name=device_name,
                status="active",
            )
            
            dl_kbps = int(group_plan.speed_limit * 1024) if group_plan and group_plan.speed_limit else None
            ul_kbps = int(group_plan.speed_limit_upload * 1024) if group_plan and group_plan.speed_limit_upload else dl_kbps
            
            if not iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps):"""

code = code.replace(old_join, new_join)

with open("sessions_app/views.py", "w", encoding="utf-8") as f:
    f.write(code)

print("sessions_app/views.py session_join_group patched.")
