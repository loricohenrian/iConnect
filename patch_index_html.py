import re

with open("portal/templates/portal/index.html", "r", encoding="utf-8") as f:
    code = f.read()

group_pass_mode_html = """
    {% if settings.enable_family_pass %}
    <!-- Group Pass Mode Toggle -->
    <div style="grid-column: 1 / -1; background: #f3f4f6; padding: 12px; border-radius: 8px; border: 1px solid #e5e7eb; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;">
        <div>
            <div style="font-weight: 700; color: #4f46e5;"><i class="bi bi-people-fill"></i> Group Pass Mode</div>
            <div style="font-size: 12px; color: var(--text-secondary);">Turn this on to buy a plan for multiple people.</div>
        </div>
        <label class="switch" style="display: flex; align-items: center; cursor: pointer;">
            <input type="checkbox" id="group-pass-toggle" style="width: 20px; height: 20px;">
        </label>
    </div>

    <!-- Group Pass Configuration (Hidden by default) -->
    <div id="group-pass-config" style="grid-column: 1 / -1; display: none; background: white; padding: 16px; border-radius: 8px; border: 2px dashed #4f46e5; flex-direction: column; gap: 12px;">
        <div style="font-size: 13px; color: var(--text-secondary);">
            1. Select a regular plan above.<br>
            2. Choose how many people will connect below.<br>
            3. The total price will be multiplied by the number of people.
        </div>
        
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div style="font-weight: 600; font-size: 14px;">Number of Devices:</div>
            <div style="display: flex; align-items: center; gap: 12px; background: #f9fafb; padding: 4px 8px; border-radius: 8px; border: 1px solid var(--border-color);">
                <button type="button" id="fp-minus" style="border:none; background:#e5e7eb; border-radius:4px; width:28px; height:28px; display:flex; align-items:center; justify-content:center; cursor:pointer; font-weight:bold; font-size: 16px;">-</button>
                <span id="fp-device-count" style="font-weight:700; font-size:16px; min-width:20px; text-align:center;">2</span>
                <button type="button" id="fp-plus" style="border:none; background:#e5e7eb; border-radius:4px; width:28px; height:28px; display:flex; align-items:center; justify-content:center; cursor:pointer; font-weight:bold; font-size: 16px;">+</button>
            </div>
        </div>
        
        <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #e5e7eb; padding-top: 12px;">
            <div style="font-weight: 600; font-size: 14px;">Total Amount:</div>
            <div id="family-pass-price" style="font-size: 24px; font-weight: 800; color: #4f46e5;">₱0</div>
        </div>
    </div>
    {% endif %}
"""

code = re.sub(
    r'\{%\s*if settings\.enable_family_pass\s*%\}.*?<!-- Family Pass Block -->.*?\{%\s*endif\s*%\}',
    group_pass_mode_html.strip(),
    code,
    flags=re.DOTALL
)

with open("portal/templates/portal/index.html", "w", encoding="utf-8") as f:
    f.write(code)
print("portal/templates/portal/index.html patched.")
