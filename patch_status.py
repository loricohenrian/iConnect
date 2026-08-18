import re

with open("sessions_app/views.py", "r", encoding="utf-8") as f:
    code = f.read()

old_status = """            refresh_session_bandwidth_usage(locked_session)
            return Response(
                {
                    "status": "active",
                    "session": SessionSerializer(locked_session).data,
                    "is_whitelisted": False,
                }
            )"""

new_status = """            refresh_session_bandwidth_usage(locked_session)
            response_data = {
                "status": "active",
                "session": SessionSerializer(locked_session).data,
                "is_whitelisted": False,
            }
            if locked_session.session_group:
                response_data["group_connected"] = locked_session.session_group.connected_devices_count
                response_data["group_max"] = locked_session.session_group.max_devices
            return Response(response_data)"""

code = code.replace(old_status, new_status)

with open("sessions_app/views.py", "w", encoding="utf-8") as f:
    f.write(code)

print("sessions_app/views.py session_status patched.")
