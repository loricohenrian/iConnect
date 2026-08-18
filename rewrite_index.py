import re

with open("portal/templates/portal/index.html", "r", encoding="utf-8") as f:
    code = f.read()

# 1. We want to remove the old group pass toggle and family pass block
code = re.sub(
    r'\{%\s*if settings\.enable_family_pass\s*%\}.*?<!-- Group Pass Mode Toggle -->.*?\{%\s*endif\s*%\}',
    '',
    code,
    flags=re.DOTALL
)

# Also remove the original family pass block just in case it's still there
code = re.sub(
    r'\{%\s*if enable_family_pass\s*%\}.*?<!-- Family Pass Block -->.*?\{%\s*endif\s*%\}',
    '',
    code,
    flags=re.DOTALL
)
# And the other variant of family pass
code = re.sub(
    r'\{%\s*if enable_family_pass\s*%\}.*?id="plan-family".*?\{%\s*endif\s*%\}',
    '',
    code,
    flags=re.DOTALL
)

group_plan_btn = """
        {% empty %}
        <div class="empty-state" id="plans-empty-state">
            <p style="font-size: 32px; margin-bottom: 8px;">📵</p>
            <p>No plans available</p>
            <small>Please contact the administrator</small>
        </div>
        {% endfor %}
    </div>

    {% if enable_family_pass %}
    <div class="mt-4 text-center" style="margin-top: 16px;">
        <button type="button" class="btn btn-outline-primary" id="btn-group-plan" style="border-radius: 8px; padding: 12px 24px; font-weight: bold; width: 100%; border: 2px dashed #6366F1; color: #6366F1; background: #EEF2FF;">
            <i class="bi bi-people-fill"></i> Buy Group Plan
        </button>
    </div>
    {% endif %}
"""

code = re.sub(
    r'\{\%\s*empty\s*\%\}.*?\{\%\s*endfor\s*\%\}[\s]*</div>',
    group_plan_btn.strip(),
    code,
    flags=re.DOTALL
)

# Remove the old "Join an existing Group Pass" button
code = re.sub(
    r'<div class="mt-4 text-center" style="margin-top: 16px; margin-bottom: 8px;">\s*<button type="button" class="btn btn-outline-primary btn-sm" id="btn-show-join-group".*?</button>\s*</div>',
    '',
    code,
    flags=re.DOTALL
)

# And add it back underneath the Group Plan
new_join_btn = """
    {% if enable_family_pass %}
    <div class="mt-4 text-center" style="margin-top: 16px;">
        <button type="button" class="btn btn-outline-primary" id="btn-group-plan" style="border-radius: 8px; padding: 12px 24px; font-weight: bold; width: 100%; border: 2px dashed #6366F1; color: #6366F1; background: #EEF2FF;">
            <i class="bi bi-people-fill"></i> Buy Group Plan
        </button>
        <button type="button" class="btn btn-link mt-2 text-muted" id="btn-show-join-group" style="font-size: 14px;">
            Have a group code? Enter it here
        </button>
    </div>
    {% endif %}
"""
code = code.replace("""    {% if enable_family_pass %}
    <div class="mt-4 text-center" style="margin-top: 16px;">
        <button type="button" class="btn btn-outline-primary" id="btn-group-plan" style="border-radius: 8px; padding: 12px 24px; font-weight: bold; width: 100%; border: 2px dashed #6366F1; color: #6366F1; background: #EEF2FF;">
            <i class="bi bi-people-fill"></i> Buy Group Plan
        </button>
    </div>
    {% endif %}""", new_join_btn)


# Now we add the Group Plan Modal just below the Join Group Modal
group_modal = """
<!-- Group Plan Modal -->
<div id="groupPlanModal" class="modal-overlay" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); z-index:2000; align-items:center; justify-content:center;">
    <div class="card animate-fadeIn" style="width: 90%; max-width: 400px; margin: auto;">
        <h3 class="mb-sm"><i class="bi bi-people-fill"></i> Group Plan</h3>
        <p class="text-small text-muted mb-md">Buy a plan for multiple people.</p>
        
        <div class="form-group mb-md">
            <label class="form-label">1. Choose a Plan</label>
            <select id="group-plan-select" class="form-input w-100" style="padding: 10px; font-size: 14px;">
                {% for plan in plans %}
                    <option value="{{ plan.id }}" data-price="{{ plan.price }}">₱{{ plan.price }} - {{ plan.duration_display }}</option>
                {% endfor %}
            </select>
        </div>
        
        <div class="form-group mb-md" style="display: flex; justify-content: space-between; align-items: center;">
            <label class="form-label m-0">2. How many people?</label>
            <div style="display: flex; align-items: center; gap: 12px; background: #f9fafb; padding: 4px 8px; border-radius: 8px; border: 1px solid var(--border-color);">
                <button type="button" id="gp-minus" style="border:none; background:#e5e7eb; border-radius:4px; width:28px; height:28px; display:flex; align-items:center; justify-content:center; cursor:pointer; font-weight:bold; font-size: 16px;">-</button>
                <span id="gp-device-count" style="font-weight:700; font-size:16px; min-width:20px; text-align:center;">2</span>
                <button type="button" id="gp-plus" style="border:none; background:#e5e7eb; border-radius:4px; width:28px; height:28px; display:flex; align-items:center; justify-content:center; cursor:pointer; font-weight:bold; font-size: 16px;">+</button>
            </div>
        </div>
        
        <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #e5e7eb; padding-top: 12px; margin-bottom: 16px;">
            <div style="font-weight: 600; font-size: 14px;">Total Amount:</div>
            <div id="group-plan-price" style="font-size: 24px; font-weight: 800; color: #4f46e5;">₱0</div>
        </div>
        
        <div class="d-flex" style="gap: 8px; flex-direction: column;">
            <button type="button" class="btn btn-primary w-100" id="btn-group-request-slot">
                <i class="bi bi-hourglass-split"></i> Request Coin Slot
            </button>
            <button type="button" class="btn btn-secondary w-100" id="btn-cancel-group-plan">Cancel</button>
        </div>
    </div>
</div>
"""

code = code.replace("<!-- Join Group Modal -->", group_modal + "\n<!-- Join Group Modal -->")

with open("portal/templates/portal/index.html", "w", encoding="utf-8") as f:
    f.write(code)

print("portal/templates/portal/index.html rewritten.")
