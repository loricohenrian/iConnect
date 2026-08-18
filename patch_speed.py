import re

with open("sessions_app/views.py", "r", encoding="utf-8") as f:
    code = f.read()

old_speed1 = """            elif existing.session_group:
                dl_kbps = int(existing.plan.speed_limit * 1024) if existing.plan and existing.plan.speed_limit else None
                ul_kbps = int(existing.plan.speed_limit_upload * 1024) if existing.plan and existing.plan.speed_limit_upload else dl_kbps
                iptables.allow_device(mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)"""
                
# Wait, let's just make sure everything is using `plan`.
old_speed2 = """        if is_group_pass:
            dl_kbps = int(settings_obj.family_pass_speed_limit * 1024)
            ul_kbps = int(settings_obj.family_pass_speed_limit_upload * 1024)
        else:"""

new_speed2 = """        if False:
            pass
        else:"""
        
# Actually, I'll just use regex to replace it completely
code = re.sub(
    r'if is_group_pass:\s+dl_kbps = int\(settings_obj\.family_pass_speed_limit \* 1024\)\s+ul_kbps = int\(settings_obj\.family_pass_speed_limit_upload \* 1024\)\s+else:',
    'if False:\n            pass\n        else:',
    code
)

with open("sessions_app/views.py", "w", encoding="utf-8") as f:
    f.write(code)

print("sessions_app/views.py session_start speed patched.")
