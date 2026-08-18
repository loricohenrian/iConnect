import re

with open("sessions_app/views.py", "r", encoding="utf-8") as f:
    code = f.read()

old_block = """    return {
        "id": coin_request.id,
        "purpose": coin_request.purpose,
        "status": coin_request.status,
        "mac_address": coin_request.mac_address,
        "expected_amount": coin_request.expected_amount,
        "credited_amount": coin_request.credited_amount,
        "queue_position": _coin_request_queue_position(coin_request),
        "expires_at": coin_request.expires_at,
        "ready_to_start": (
            coin_request.expected_amount > 0 and 
            coin_request.credited_amount >= coin_request.expected_amount
        ),
    }"""

new_block = """    return {
        "id": coin_request.id,
        "purpose": coin_request.purpose,
        "status": coin_request.status,
        "mac_address": coin_request.mac_address,
        "expected_amount": coin_request.expected_amount,
        "credited_amount": coin_request.credited_amount,
        "queue_position": _coin_request_queue_position(coin_request),
        "expires_at": coin_request.expires_at,
        "is_group_pass": coin_request.is_group_pass,
        "group_pass_devices": coin_request.group_pass_devices,
        "plan_id": coin_request.plan_id,
        "ready_to_start": (
            coin_request.expected_amount > 0 and 
            coin_request.credited_amount >= coin_request.expected_amount
        ),
    }"""

if old_block in code:
    code = code.replace(old_block, new_block, 1)

with open("sessions_app/views.py", "w", encoding="utf-8") as f:
    f.write(code)

print("sessions_app/views.py patched.")
