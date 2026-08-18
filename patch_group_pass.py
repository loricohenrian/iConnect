import re

with open("sessions_app/views.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update _get_or_create_start_coin_request
code = code.replace(
    "def _get_or_create_start_coin_request(mac_address, ip_address, plan):",
    "def _get_or_create_start_coin_request(mac_address, ip_address, plan, is_group_pass=False, group_pass_devices=1):"
)
code = code.replace(
    "if existing_request.plan_id == plan.id:",
    "if existing_request.plan_id == plan.id and existing_request.is_group_pass == is_group_pass and existing_request.group_pass_devices == group_pass_devices:"
)
code = code.replace(
    "credited_amount >= plan.price",
    "credited_amount >= (plan.price * group_pass_devices if is_group_pass else plan.price)"
)
code = code.replace(
    "expected_amount=plan.price,",
    "expected_amount=plan.price * group_pass_devices if is_group_pass else plan.price,\n        is_group_pass=is_group_pass,\n        group_pass_devices=group_pass_devices,"
)

# 2. Update session_start_request
code = code.replace(
    """    plan = None
    if not is_group_pass:
        try:
            plan = Plan.objects.get(id=plan_id, is_active=True)
        except Plan.DoesNotExist:
            return Response(
                {"error": "Plan not found or inactive"},
                status=status.HTTP_404_NOT_FOUND,
            )""",
    """    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"error": "Plan not found or inactive"},
            status=status.HTTP_404_NOT_FOUND,
        )"""
)
code = code.replace(
    """        coin_request, created = _get_or_create_start_coin_request(
            mac_address, ip_address, plan,
        )""",
    """        coin_request, created = _get_or_create_start_coin_request(
            mac_address, ip_address, plan, is_group_pass=is_group_pass, group_pass_devices=group_pass_devices
        )"""
)

# 3. Update session_start
# In session_start, we need to handle group pass creation using plan
code = code.replace(
    """    plan = None
    if not is_group_pass:
        try:
            plan = Plan.objects.get(id=plan_id)
        except Plan.DoesNotExist:
            return Response({"error": "Plan not found"}, status=status.HTTP_404_NOT_FOUND)""",
    """    try:
        plan = Plan.objects.get(id=plan_id)
    except Plan.DoesNotExist:
        return Response({"error": "Plan not found"}, status=status.HTTP_404_NOT_FOUND)"""
)

code = code.replace(
    """    # Verify payment
    expected_amount = 0
    if not is_group_pass:
        expected_amount = plan.price
    else:
        settings_obj = SystemSettings.get_settings()
        expected_amount = settings_obj.family_pass_base_rate + (max(0, group_pass_devices - 1) * settings_obj.family_pass_device_rate)""",
    """    # Verify payment
    expected_amount = plan.price * group_pass_devices if is_group_pass else plan.price"""
)

code = code.replace(
    """        # Calculate duration based on expected_amount (since it might be dynamic)
        # But wait, for family pass, duration was passed in from frontend. 
        duration_minutes = group_pass_duration_minutes
        
        session_group = SessionGroup.objects.create(
            group_code=_generate_group_code(),
            max_devices=group_pass_devices,
            total_price=expected_amount,
            duration_minutes=duration_minutes,
            time_out=timezone.now() + timezone.timedelta(minutes=duration_minutes),
        )""",
    """        duration_minutes = plan.duration_minutes
        
        session_group = SessionGroup.objects.create(
            group_code=_generate_group_code(),
            max_devices=group_pass_devices,
            total_price=expected_amount,
            duration_minutes=duration_minutes,
            time_out=timezone.now() + timezone.timedelta(minutes=duration_minutes),
        )"""
)


with open("sessions_app/views.py", "w", encoding="utf-8") as f:
    f.write(code)
print("sessions_app/views.py patched.")
