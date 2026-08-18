import re

with open("static/js/portal.js", "r", encoding="utf-8") as f:
    code = f.read()

# Remove the old family pass UI logic and replace it with group pass logic
old_fp_logic = """    // Family Pass Elements
    let fpDevices = 1;
    const fpPlus = document.getElementById("fp-plus");
    const fpMinus = document.getElementById("fp-minus");
    const fpCount = document.getElementById("fp-device-count");
    const fpPrice = document.getElementById("family-pass-price");
    const fpDuration = document.getElementById("family-pass-duration");

    function updateFamilyPassPrice() {
        if (!fpPrice || !fpDuration) return;
        const durStr = fpDuration.options[fpDuration.selectedIndex].text;
        const durHours = durStr.includes('Hour') ? parseInt(durStr.split(' ')[0]) : 1;
        
        // This is a rough UI estimation. Real price is calculated in backend.
        // We'll just let the backend handle the exact price, or we can parse it from template vars if needed.
        // For now, let's just show "Calculating..." or update the UI text to let user know it's dynamic
        fpPrice.innerText = `₱ Dynamic`; 
    }

    if (fpPlus && fpMinus && fpCount) {
        fpPlus.addEventListener("click", (e) => {
            e.stopPropagation();
            if (fpDevices < 6) { fpDevices++; fpCount.innerText = fpDevices; updateFamilyPassPrice(); }
        });
        fpMinus.addEventListener("click", (e) => {
            e.stopPropagation();
            if (fpDevices > 1) { fpDevices--; fpCount.innerText = fpDevices; updateFamilyPassPrice(); }
        });
    }
    if (fpDuration) {
        fpDuration.addEventListener("change", (e) => {
            updateFamilyPassPrice();
        });
        fpDuration.addEventListener("click", (e) => {
            e.stopPropagation();
        });
    }"""

new_fp_logic = """    // Group Pass UI Elements
    const toggleGroup = document.getElementById("group-pass-toggle");
    const configGroup = document.getElementById("group-pass-config");
    const fpPlus = document.getElementById("fp-plus");
    const fpMinus = document.getElementById("fp-minus");
    const fpCount = document.getElementById("fp-device-count");
    const fpPrice = document.getElementById("family-pass-price");
    let fpDevices = 2; // Default to 2 devices for group pass
    let currentPlanPrice = 0;

    function updateGroupPassPrice() {
        if (fpPrice && toggleGroup && toggleGroup.checked) {
            fpPrice.innerText = `₱${currentPlanPrice * fpDevices}`;
        }
    }

    if (toggleGroup && configGroup) {
        toggleGroup.addEventListener("change", () => {
            if (toggleGroup.checked) {
                configGroup.style.display = "flex";
                const selectedPlanInput = document.getElementById("selected-plan");
                if (!currentPlanPrice && selectedPlanInput && selectedPlanInput.value) {
                    const card = document.querySelector(`.plan-card[data-plan-id="${selectedPlanInput.value}"]`);
                    if (card) {
                        const priceText = card.querySelector('.plan-price').innerText.replace('₱', '');
                        currentPlanPrice = parseInt(priceText) || 0;
                    }
                }
            } else {
                configGroup.style.display = "none";
            }
            updateGroupPassPrice();
        });
    }

    if (fpPlus && fpMinus && fpCount) {
        fpPlus.addEventListener("click", (e) => {
            e.stopPropagation();
            if (fpDevices < 10) { fpDevices++; fpCount.innerText = fpDevices; updateGroupPassPrice(); }
        });
        fpMinus.addEventListener("click", (e) => {
            e.stopPropagation();
            if (fpDevices > 2) { fpDevices--; fpCount.innerText = fpDevices; updateGroupPassPrice(); }
        });
    }"""

code = code.replace(old_fp_logic, new_fp_logic)


old_card_click = """            if (selectedPlanInput) {
                selectedPlanInput.value = card.dataset.isGroup ? "group" : card.dataset.planId;
            }"""

new_card_click = """            if (selectedPlanInput) {
                selectedPlanInput.value = card.dataset.planId;
                const priceText = card.querySelector('.plan-price').innerText.replace('₱', '');
                currentPlanPrice = parseInt(priceText) || 0;
                updateGroupPassPrice();
            }"""

code = code.replace(old_card_click, new_card_click)

code = code.replace("""    const selectedPlanId = () => {
        if (selectedPlanInput.value === "group") return "group";
        const value = Number.parseInt(selectedPlanInput.value, 10);
        return Number.isInteger(value) && value > 0 ? value : null;
    };""", """    const selectedPlanId = () => {
        const value = Number.parseInt(selectedPlanInput.value, 10);
        return Number.isInteger(value) && value > 0 ? value : null;
    };""")

old_body_req = """                body: JSON.stringify({
                    mac_address: macAddress,
                    plan_id: planId === "group" ? null : planId,
                    is_group_pass: planId === "group",
                    group_pass_devices: planId === "group" ? parseInt(document.getElementById("fp-device-count")?.innerText || "1") : null,
                    group_pass_duration_minutes: planId === "group" ? parseInt(document.getElementById("family-pass-duration")?.value || "60") : null,
                }),"""
                
new_body_req = """                body: JSON.stringify({
                    mac_address: macAddress,
                    plan_id: planId,
                    is_group_pass: document.getElementById("group-pass-toggle")?.checked || false,
                    group_pass_devices: document.getElementById("group-pass-toggle")?.checked ? parseInt(document.getElementById("fp-device-count")?.innerText || "2") : null,
                }),"""

code = code.replace(old_body_req, new_body_req)


code = code.replace(
"""        const nextPlanId = planIdValue === "group" ? "group" : Number.parseInt(planIdValue, 10);
        if (nextPlanId !== "group" && (!Number.isInteger(nextPlanId) || nextPlanId <= 0)) {""",
"""        const nextPlanId = Number.parseInt(planIdValue, 10);
        if (!Number.isInteger(nextPlanId) || nextPlanId <= 0) {"""
)


with open("static/js/portal.js", "w", encoding="utf-8") as f:
    f.write(code)

print("static/js/portal.js patched for group pass mode.")
