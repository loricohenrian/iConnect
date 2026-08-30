import os
import re

portal_js_path = r'c:\Users\Henrian\Desktop\iConnect\static\js\portal.js'
views_path = r'c:\Users\Henrian\Desktop\iConnect\sessions_app\views.py'

# 1. Fix portal.js duplicate listener
with open(portal_js_path, 'r', encoding='utf-8') as f:
    js_content = f.read()

# We look for the duplicate block at the bottom
# It starts with "// Join Group Modal logic" or something similar if we appended it.
# Let's find the exact block and remove it.

duplicate_block_start = js_content.find('// Join Group Modal logic')
if duplicate_block_start != -1:
    js_content = js_content[:duplicate_block_start]
    with open(portal_js_path, 'w', encoding='utf-8') as f:
        f.write(js_content)
    print("Fixed portal.js duplicate listener.")

# 2. Fix sessions_app/views.py race condition
with open(views_path, 'r', encoding='utf-8') as f:
    views_content = f.read()

old_logic = """    # Check if all slots are used
    if group.is_full():
        return Response(
            {"error": f"This group pass is full ({group.redeemed_count}/{group.max_devices} slots used)."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # MAC lock — prevent same device from redeeming twice
    if group.has_mac_redeemed(mac_address):
        return Response(
            {"error": "Your device has already redeemed this group pass."},
            status=status.HTTP_409_CONFLICT,
        )

    group_plan = group.plan
    if not group_plan:
        return Response({"error": "This group pass has no plan configured. Please contact the operator."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            # Each device gets the FULL plan duration — fully independent session
            session = Session.objects.create("""

new_logic = """    group_plan = group.plan
    if not group_plan:
        return Response({"error": "This group pass has no plan configured. Please contact the operator."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            # Lock the group row to prevent race conditions from concurrent clicks
            locked_group = SessionGroup.objects.select_for_update().get(id=group.id)
            
            # Check if all slots are used
            if locked_group.is_full():
                return Response(
                    {"error": f"This group pass is full ({locked_group.redeemed_count}/{locked_group.max_devices} slots used)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # MAC lock — prevent same device from redeeming twice
            if locked_group.has_mac_redeemed(mac_address):
                return Response(
                    {"error": "Your device has already redeemed this group pass."},
                    status=status.HTTP_409_CONFLICT,
                )

            # Each device gets the FULL plan duration — fully independent session
            session = Session.objects.create("""

if old_logic in views_content:
    views_content = views_content.replace(old_logic, new_logic)
    
    # We also need to fix `if group.redeemed_count >= group.max_devices:` lower down to use locked_group
    views_content = views_content.replace(
        "if group.redeemed_count >= group.max_devices:",
        "if locked_group.redeemed_count >= locked_group.max_devices:"
    )
    views_content = views_content.replace(
        "group.status = \"exhausted\"\n                group.save(update_fields=[\"status\"])",
        "locked_group.status = \"exhausted\"\n                locked_group.save(update_fields=[\"status\"])"
    )

    with open(views_path, 'w', encoding='utf-8') as f:
        f.write(views_content)
    print("Fixed views.py race condition.")
else:
    print("Could not find the old logic block in views.py")
