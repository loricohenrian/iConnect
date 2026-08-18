import re

with open("sessions_app/views.py", "r", encoding="utf-8") as f:
    code = f.read()

old_call = """        coin_request, created = _get_or_create_start_coin_request(
            mac_address, ip_address, plan,
            is_group_pass=is_group_pass,
            group_pass_devices=group_pass_devices,
            group_pass_duration_minutes=group_pass_duration_minutes,
            settings_obj=settings_obj
        )"""

new_call = """        coin_request, created = _get_or_create_start_coin_request(
            mac_address, ip_address, plan,
            is_group_pass=is_group_pass,
            group_pass_devices=group_pass_devices
        )"""

code = code.replace(old_call, new_call)

old_call2 = """            coin_request, _ = _get_or_create_start_coin_request(
                mac_address, ip_address, plan,
                is_group_pass=is_group_pass,
                group_pass_devices=group_pass_devices,
                group_pass_duration_minutes=group_pass_duration_minutes,
                settings_obj=settings_obj
            )"""

new_call2 = """            coin_request, _ = _get_or_create_start_coin_request(
                mac_address, ip_address, plan,
                is_group_pass=is_group_pass,
                group_pass_devices=group_pass_devices
            )"""

code = code.replace(old_call2, new_call2)

with open("sessions_app/views.py", "w", encoding="utf-8") as f:
    f.write(code)

print("sessions_app/views.py calls patched.")
