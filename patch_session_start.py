import re

with open("sessions_app/views.py", "r", encoding="utf-8") as f:
    code = f.read()

old_chunk = """    if not is_group_pass:
        try:
            plan = Plan.objects.get(id=plan_id, is_active=True)
            expected_amount = plan.price
            duration_minutes = plan.duration_minutes
        except Plan.DoesNotExist:
            return Response({"error": "Plan not found or inactive"}, status=status.HTTP_404_NOT_FOUND)
    else:
        hours = group_pass_duration_minutes / 60.0
        additional_devices = max(0, group_pass_devices - 1)
        expected_amount = int((settings_obj.family_pass_base_rate + (additional_devices * settings_obj.family_pass_device_rate)) * hours)
        duration_minutes = group_pass_duration_minutes"""

new_chunk = """    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
        expected_amount = plan.price * group_pass_devices if is_group_pass else plan.price
        duration_minutes = plan.duration_minutes
    except Plan.DoesNotExist:
        return Response({"error": "Plan not found or inactive"}, status=status.HTTP_404_NOT_FOUND)"""

if old_chunk in code:
    code = code.replace(old_chunk, new_chunk)
else:
    print("WARNING: Old chunk not found in session_start.")

with open("sessions_app/views.py", "w", encoding="utf-8") as f:
    f.write(code)

print("sessions_app/views.py session_start patched.")
