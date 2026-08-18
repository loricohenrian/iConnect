import re

with open("dashboard/templates/dashboard/settings.html", "r", encoding="utf-8") as f:
    code = f.read()

# Replace the grid containing the obsolete fields
old_grid = """            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem;">
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
            </div>"""

new_grid = """            <!-- Obsolete fields removed: Base Rate, Device Rate, Max Devices, and Speed Limits are now dynamically inherited from the selected Plan -->
            <input type="hidden" name="family_pass_base_rate" value="{{ settings.family_pass_base_rate }}">
            <input type="hidden" name="family_pass_device_rate" value="{{ settings.family_pass_device_rate }}">
            <input type="hidden" name="family_pass_max_devices" value="{{ settings.family_pass_max_devices }}">
            <input type="hidden" name="family_pass_speed_limit" value="{{ settings.family_pass_speed_limit }}">
            <input type="hidden" name="family_pass_speed_limit_upload" value="{{ settings.family_pass_speed_limit_upload }}">
"""

code = code.replace(old_grid, new_grid)

with open("dashboard/templates/dashboard/settings.html", "w", encoding="utf-8") as f:
    f.write(code)

print("settings.html cleaned up.")
