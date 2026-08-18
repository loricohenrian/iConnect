import re

with open("portal/templates/portal/index.html", "r", encoding="utf-8") as f:
    code = f.read()

family_pass_html = """
        {% if enable_family_pass %}
        <div class="plan-card" data-is-group="true" id="plan-family" style="border-color: #6366F1;">
            <div class="plan-popular" style="background: #6366F1; color: white;">Family Pass</div>
            <div class="plan-price" id="family-pass-price">₱{{ family_pass_base_rate }}</div>
            <div class="plan-duration">
                <select id="family-pass-duration" class="form-control form-control-sm" style="display:inline-block; width:auto; font-size:12px; margin-top:4px; margin-bottom:4px; padding:2px 8px;">
                    <option value="60">1 Hour</option>
                    <option value="180">3 Hours</option>
                    <option value="360">6 Hours</option>
                    <option value="720">12 Hours</option>
                    <option value="1440">24 Hours</option>
                </select>
            </div>
            <div class="plan-speed" style="font-size: 13px; color: var(--text-secondary); margin-top: 8px; text-transform: none; display: flex; align-items: center; justify-content: center; gap: 8px;">
                <button type="button" class="btn btn-sm btn-outline-secondary" id="fp-minus" style="padding: 2px 8px; line-height: 1;">-</button>
                <span id="fp-device-count" style="font-weight: bold; width: 14px; text-align: center;">1</span> <i class="bi bi-phone"></i>
                <button type="button" class="btn btn-sm btn-outline-secondary" id="fp-plus" style="padding: 2px 8px; line-height: 1;">+</button>
            </div>
            <div style="font-size: 11px; margin-top: 6px; color: #6366F1;">
                +₱{{ family_pass_device_rate }}/hr per extra device
            </div>
        </div>
        {% endif %}
"""

# Insert family pass after plans empty state
code = code.replace("{% empty %}", family_pass_html + "        {% empty %}")

# Add Join Group button
join_html = """
<div class="mt-4 text-center" style="margin-top: 16px; margin-bottom: 8px;">
    <button type="button" class="btn btn-outline-primary btn-sm" id="btn-show-join-group" style="border-radius: 50px; padding: 6px 16px;">
        <i class="bi bi-people-fill"></i> Join an existing Group Pass
    </button>
</div>

<!-- Join Group Modal -->
<div id="joinGroupModal" class="modal-overlay" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); z-index:2000; align-items:center; justify-content:center;">
    <div class="card animate-fadeIn" style="width: 90%; max-width: 400px; margin: auto;">
        <h3 class="mb-sm">Join Group Pass</h3>
        <p class="text-small text-muted mb-md">Enter the 6-character group code to share the session.</p>
        <div class="form-group mb-md">
            <input type="text" id="join-group-code" class="form-control text-center" placeholder="e.g. A1B2C3" maxlength="6" style="font-size: 24px; letter-spacing: 2px; text-transform: uppercase;">
        </div>
        <div id="join-group-error" class="alert alert-danger" style="display:none; padding: 8px; font-size: 13px;"></div>
        <div class="d-flex" style="gap: 8px;">
            <button type="button" class="btn btn-secondary flex-grow-1" id="btn-cancel-join">Cancel</button>
            <button type="button" class="btn btn-primary flex-grow-1" id="btn-submit-join">Join</button>
        </div>
    </div>
</div>
"""

# Insert before start-session-panel
code = code.replace('<div class="mt-md" id="start-session-panel">', join_html + '\n<div class="mt-md" id="start-session-panel">')

with open("portal/templates/portal/index.html", "w", encoding="utf-8") as f:
    f.write(code)

print("Patched index.html successfully!")
