import re

with open("static/js/portal.js", "r", encoding="utf-8") as f:
    code = f.read()

# Replace initPlanSelection to handle family pass selection
code = code.replace(
"""function initPlanSelection() {
    const planCards = document.querySelectorAll(".plan-card");
    const selectedPlanInput = document.getElementById("selected-plan");

    planCards.forEach((card) => {
        card.addEventListener("click", () => {
            planCards.forEach((item) => item.classList.remove("selected"));
            card.classList.add("selected");

            if (selectedPlanInput) {
                selectedPlanInput.value = card.dataset.planId;
            }

            const requestBtn = document.getElementById("request-slot-btn");
            if (requestBtn) {
                requestBtn.disabled = false;
            }

            if (typeof window.onPortalPlanSelected === "function") {
                window.onPortalPlanSelected(card.dataset.planId);
            }
        });
    });
}""",
"""function initPlanSelection() {
    const planCards = document.querySelectorAll(".plan-card");
    const selectedPlanInput = document.getElementById("selected-plan");

    // Family Pass Elements
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
    }

    planCards.forEach((card) => {
        card.addEventListener("click", () => {
            planCards.forEach((item) => item.classList.remove("selected"));
            card.classList.add("selected");

            if (selectedPlanInput) {
                selectedPlanInput.value = card.dataset.isGroup ? "group" : card.dataset.planId;
            }

            const requestBtn = document.getElementById("request-slot-btn");
            if (requestBtn) {
                requestBtn.disabled = false;
            }

            if (typeof window.onPortalPlanSelected === "function") {
                window.onPortalPlanSelected(selectedPlanInput.value);
            }
        });
    });
}"""
)

# Patch initProductionStartFlow payload creation
code = code.replace(
"""    const selectedPlanId = () => {
        const value = Number.parseInt(selectedPlanInput.value, 10);
        return Number.isInteger(value) && value > 0 ? value : null;
    };

    requestBtn.addEventListener("click", async () => {
        const planId = selectedPlanId();
        if (!planId) {
            setStartFlowMessage("Select a plan before requesting a coin slot.", "warning");
            return;
        }""",
"""    const selectedPlanId = () => {
        if (selectedPlanInput.value === "group") return "group";
        const value = Number.parseInt(selectedPlanInput.value, 10);
        return Number.isInteger(value) && value > 0 ? value : null;
    };

    requestBtn.addEventListener("click", async () => {
        const planId = selectedPlanId();
        if (!planId) {
            setStartFlowMessage("Select a plan before requesting a coin slot.", "warning");
            return;
        }"""
)

code = code.replace(
"""                body: JSON.stringify({
                    mac_address: macAddress,
                    plan_id: planId,
                }),""",
"""                body: JSON.stringify({
                    mac_address: macAddress,
                    plan_id: planId === "group" ? null : planId,
                    is_group_pass: planId === "group",
                    group_pass_devices: planId === "group" ? parseInt(document.getElementById("fp-device-count")?.innerText || "1") : null,
                    group_pass_duration_minutes: planId === "group" ? parseInt(document.getElementById("family-pass-duration")?.value || "60") : null,
                }),"""
)

# Join Group logic
join_script = """
    const btnShowJoin = document.getElementById("btn-show-join-group");
    const joinModal = document.getElementById("joinGroupModal");
    const btnCancelJoin = document.getElementById("btn-cancel-join");
    const btnSubmitJoin = document.getElementById("btn-submit-join");
    const joinCodeInput = document.getElementById("join-group-code");
    const joinError = document.getElementById("join-group-error");

    if (btnShowJoin && joinModal) {
        btnShowJoin.addEventListener("click", () => {
            joinModal.style.display = "flex";
            joinCodeInput.value = "";
            joinError.style.display = "none";
            joinCodeInput.focus();
        });

        btnCancelJoin.addEventListener("click", () => {
            joinModal.style.display = "none";
        });

        btnSubmitJoin.addEventListener("click", async () => {
            const code = joinCodeInput.value.trim().toUpperCase();
            if (!code || code.length !== 6) {
                joinError.textContent = "Please enter a valid 6-character code.";
                joinError.style.display = "block";
                return;
            }

            btnSubmitJoin.disabled = true;
            btnSubmitJoin.textContent = "Joining...";
            joinError.style.display = "none";

            try {
                const response = await fetch("/api/session/join-group/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": getCSRFToken(),
                    },
                    body: JSON.stringify({
                        mac_address: macAddress,
                        group_code: code,
                    }),
                });
                const data = await parseJsonSafe(response);

                if (response.ok) {
                    window.location.href = buildPortalUrl("/session/", macAddress);
                } else {
                    joinError.textContent = data.error || "Failed to join group.";
                    joinError.style.display = "block";
                }
            } catch (error) {
                joinError.textContent = "Network error while joining group.";
                joinError.style.display = "block";
            } finally {
                btnSubmitJoin.disabled = false;
                btnSubmitJoin.textContent = "Join";
            }
        });
    }
"""

code = code.replace(
"""window.onPortalPlanSelected = (planIdValue) => {""",
join_script + "\n    window.onPortalPlanSelected = (planIdValue) => {"
)

code = code.replace(
"""        const nextPlanId = Number.parseInt(planIdValue, 10);
        if (!Number.isInteger(nextPlanId) || nextPlanId <= 0) {
            requestBtn.disabled = true;
            startBtn.disabled = true;
            startBtn.dataset.readyToStart = "0";
            return;
        }""",
"""        const nextPlanId = planIdValue === "group" ? "group" : Number.parseInt(planIdValue, 10);
        if (nextPlanId !== "group" && (!Number.isInteger(nextPlanId) || nextPlanId <= 0)) {
            requestBtn.disabled = true;
            startBtn.disabled = true;
            startBtn.dataset.readyToStart = "0";
            return;
        }"""
)

with open("static/js/portal.js", "w", encoding="utf-8") as f:
    f.write(code)

print("Patched portal.js successfully!")
