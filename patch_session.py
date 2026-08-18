import re

with open("portal/templates/portal/session.html", "r", encoding="utf-8") as f:
    code = f.read()

old_badge = """        {% if session.session_group %}
        <span class="badge badge-active" style="background: #6366F1;">Family Pass - Code: {{ session.session_group.group_code }}</span>
        <div style="font-size: 11px; margin-top: 8px; color: var(--text-secondary);">
            Share this 6-character code with your family to let them join the session.
        </div>
        {% else %}"""

new_badge = """        {% if session.session_group %}
        <span class="badge badge-active" style="background: #6366F1;">Group Plan - Code: {{ session.session_group.group_code }}</span>
        <div id="group-plan-status" style="font-weight: bold; margin-top: 8px; color: #4f46e5;">
            Group Plan: {{ session.session_group.session_set.count }} / {{ session.session_group.max_devices }} devices connected
        </div>
        <div style="font-size: 11px; margin-top: 4px; color: var(--text-secondary);">
            Share this 6-character code with your group to let them join the session.
        </div>
        {% else %}"""

code = code.replace(old_badge, new_badge)

with open("portal/templates/portal/session.html", "w", encoding="utf-8") as f:
    f.write(code)

print("portal/templates/portal/session.html patched.")
