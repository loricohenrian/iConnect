import re

with open("dashboard/templates/dashboard/settings.html", "r", encoding="utf-8") as f:
    code = f.read()

family_pass_settings = """
    <!-- Family Pass Settings -->
    <div class="chart-card animate-fadeIn" style="animation-delay: 250ms; grid-column: 1 / -1;">
        <h3 class="card-title mb-md"><i class="bi bi-people-fill"></i> Family/Group Pass</h3>
        
        <form method="POST" action="">
            {% csrf_token %}
            <div class="form-group mb-lg">
                <label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer;">
                    <input type="checkbox" name="enable_family_pass" {% if settings.enable_family_pass %}checked{% endif %}>
                    <span style="font-weight: 600;">Enable Family Pass</span>
                </label>
                <small style="color: var(--text-secondary); display: block; margin-left: 1.75rem;">
                    Allow users to purchase a group plan and share a 6-character code with their family to connect multiple devices.
                </small>
            </div>

            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem;">
                <div class="form-group">
                    <label class="form-label" style="font-size: 0.85rem;">Base Rate (₱/hr)</label>
                    <input type="number" min="1" class="form-input" name="family_pass_base_rate" value="{{ settings.family_pass_base_rate }}" required>
                </div>
                <div class="form-group">
                    <label class="form-label" style="font-size: 0.85rem;">Device Rate (₱/hr per extra device)</label>
                    <input type="number" min="0" class="form-input" name="family_pass_device_rate" value="{{ settings.family_pass_device_rate }}" required>
                </div>
                <div class="form-group">
                    <label class="form-label" style="font-size: 0.85rem;">Max Devices</label>
                    <input type="number" min="2" class="form-input" name="family_pass_max_devices" value="{{ settings.family_pass_max_devices }}" required>
                </div>
                <div class="form-group">
                    <label class="form-label" style="font-size: 0.85rem;">Download Speed Limit (Mbps)</label>
                    <input type="number" step="0.1" class="form-input" name="family_pass_speed_limit" value="{{ settings.family_pass_speed_limit }}" required>
                </div>
                <div class="form-group">
                    <label class="form-label" style="font-size: 0.85rem;">Upload Speed Limit (Mbps)</label>
                    <input type="number" step="0.1" class="form-input" name="family_pass_speed_limit_upload" value="{{ settings.family_pass_speed_limit_upload }}" required>
                </div>
            </div>
            
            <div class="form-group mt-xl" style="text-align: right;">
                <button type="submit" class="btn btn-primary">
                    <i class="bi bi-save"></i> Save Family Pass Settings
                </button>
            </div>
        </form>
    </div>
"""

# Insert before endblock
code = code.replace("{% endblock %}", family_pass_settings + "\n{% endblock %}")

with open("dashboard/templates/dashboard/settings.html", "w", encoding="utf-8") as f:
    f.write(code)

print("Patched settings.html successfully!")
