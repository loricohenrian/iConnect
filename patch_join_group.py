import re

with open("sessions_app/views.py", "r", encoding="utf-8") as f:
    code = f.read()

# session_join_group
old_create = """        session = Session.objects.create(
            mac_address=mac_address,
            plan=None,
            session_group=group,
            time_in=timezone.now(),
            duration_minutes_purchased=remaining_minutes,
            amount_paid=0,
            ip_address=ip_address,
            device_name=device_name,
            status="active",
        )"""

new_create = """        # Inherit plan from the group's creator session
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
        )"""

code = code.replace(old_create, new_create)

# And in iptables rate limiting inside session_join_group:
old_iptables = """            elif existing.session_group:
                iptables.allow_device(mac_address, rate_kbps=int(settings_obj.family_pass_speed_limit * 1024), upload_kbps=int(settings_obj.family_pass_speed_limit_upload * 1024))"""

new_iptables = """            elif existing.session_group:
                dl_kbps = int(existing.plan.speed_limit * 1024) if existing.plan and existing.plan.speed_limit else None
                ul_kbps = int(existing.plan.speed_limit_upload * 1024) if existing.plan and existing.plan.speed_limit_upload else dl_kbps
                iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)"""

code = code.replace(old_iptables, new_iptables)


with open("sessions_app/views.py", "w", encoding="utf-8") as f:
    f.write(code)

print("sessions_app/views.py session_join_group patched.")
