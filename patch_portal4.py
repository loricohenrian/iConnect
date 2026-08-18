import re

with open("static/js/portal.js", "r", encoding="utf-8") as f:
    code = f.read()

# Replace startBtn fetch
old_fetch = """        try {
            const response = await fetch("/api/session/start/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken(),
                },
                body: JSON.stringify({
                    mac_address: macAddress,
                    plan_id: planId,
                    is_group_pass: document.getElementById("group-pass-toggle")?.checked || false,
                    group_pass_devices: document.getElementById("group-pass-toggle")?.checked ? parseInt(document.getElementById("fp-device-count")?.innerText || "2") : null,
                }),
            });"""

new_fetch = """        try {
            const payload = {
                mac_address: macAddress,
                plan_id: planId
            };
            if (state.isGroupPass) {
                payload.is_group_pass = true;
                payload.group_pass_devices = state.groupDevices;
            }

            const response = await fetch("/api/session/start/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken(),
                },
                body: JSON.stringify(payload),
            });"""

if old_fetch in code:
    code = code.replace(old_fetch, new_fetch, 1)
else:
    print("WARNING: fetch block not found")

# Replace startBtn planId check to fallback to state.planId
old_plan_check = """    startBtn.addEventListener("click", async () => {
        const planId = selectedPlanId();
        if (!planId) {"""

new_plan_check = """    startBtn.addEventListener("click", async () => {
        const planId = state.planId || selectedPlanId();
        if (!planId) {"""

if old_plan_check in code:
    code = code.replace(old_plan_check, new_plan_check, 1)
else:
    print("WARNING: plan check block not found")


with open("static/js/portal.js", "w", encoding="utf-8") as f:
    f.write(code)

print("portal.js patched successfully.")
