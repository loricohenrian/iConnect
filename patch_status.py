import re

with open("sessions_app/views.py", "r", encoding="utf-8") as f:
    code = f.read()

old_status = """        return Response(
            {
                "status": "active",
                "remaining_minutes": int(remaining.total_seconds() / 60),
                "mac_address": session.mac_address,
                "plan_name": session.plan.name if session.plan else "Family Pass",
            }
        )"""

new_status = """        response_data = {
            "status": "active",
            "remaining_minutes": int(remaining.total_seconds() / 60),
            "mac_address": session.mac_address,
            "plan_name": session.plan.name if session.plan else "Family Pass",
        }
        
        if session.session_group:
            response_data["group_connected"] = session.session_group.session_set.filter(status='active').count()
            response_data["group_max"] = session.session_group.max_devices
            
        return Response(response_data)"""

code = code.replace(old_status, new_status)

with open("sessions_app/views.py", "w", encoding="utf-8") as f:
    f.write(code)

print("sessions_app/views.py session_status patched.")
