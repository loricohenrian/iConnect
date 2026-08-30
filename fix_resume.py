import os

views_path = r'c:\Users\Henrian\Desktop\iConnect\dashboard\views.py'

with open(views_path, 'r', encoding='utf-8') as f:
    content = f.read()

old_code = """        # Allow in firewall
        try:
            from sessions_app.iptables import allow_device
            rate = session.plan.speed_limit if session.plan else None
            ul_rate = session.plan.speed_limit_upload if session.plan else None
            allow_device(session.mac_address, rate_kbps=rate, upload_kbps=ul_rate)
        except Exception as e:
            logging.error(f"Failed to allow device on resume: {e}")"""

new_code = """        # Allow in firewall
        try:
            from sessions_app.iptables import allow_device
            dl_kbps = int(session.plan.speed_limit * 1024) if session.plan and session.plan.speed_limit else None
            ul_kbps = int(session.plan.speed_limit_upload * 1024) if session.plan and session.plan.speed_limit_upload else dl_kbps
            allow_device(session.mac_address, rate_kbps=dl_kbps, upload_kbps=ul_kbps)
        except Exception as e:
            logging.error(f"Failed to allow device on resume: {e}")"""

if old_code in content:
    content = content.replace(old_code, new_code)
    with open(views_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Fixed resume logic.")
else:
    print("Could not find code to replace.")
