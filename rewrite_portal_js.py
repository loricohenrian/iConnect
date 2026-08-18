import re

with open("static/js/portal.js", "r", encoding="utf-8") as f:
    code = f.read()

# First we need to get rid of the Group Pass Mode Toggle logic that we previously added
# We'll just replace the entire window.addEventListener('DOMContentLoaded' ... ) block related to family pass

new_js = """
    // --- Group Plan Logic ---
    const btnGroupPlan = document.getElementById("btn-group-plan");
    const groupPlanModal = document.getElementById("groupPlanModal");
    const btnCancelGroupPlan = document.getElementById("btn-cancel-group-plan");
    const gpMinus = document.getElementById("gp-minus");
    const gpPlus = document.getElementById("gp-plus");
    const gpDeviceCount = document.getElementById("gp-device-count");
    const groupPlanSelect = document.getElementById("group-plan-select");
    const groupPlanPrice = document.getElementById("group-plan-price");
    const btnGroupRequestSlot = document.getElementById("btn-group-request-slot");
    
    let currentGpDevices = 2; // Default
    
    function updateGroupPlanPrice() {
        if(!groupPlanSelect) return;
        const opt = groupPlanSelect.options[groupPlanSelect.selectedIndex];
        if(opt) {
            const price = parseInt(opt.getAttribute("data-price") || 0);
            const total = price * currentGpDevices;
            groupPlanPrice.innerText = "₱" + total;
        }
    }

    if (btnGroupPlan) {
        btnGroupPlan.addEventListener("click", () => {
            if(groupPlanModal) {
                groupPlanModal.style.display = "flex";
                updateGroupPlanPrice();
            }
        });
    }

    if (btnCancelGroupPlan) {
        btnCancelGroupPlan.addEventListener("click", () => {
            groupPlanModal.style.display = "none";
        });
    }

    if (gpMinus && gpPlus && gpDeviceCount) {
        gpMinus.addEventListener("click", () => {
            if (currentGpDevices > 2) {
                currentGpDevices--;
                gpDeviceCount.innerText = currentGpDevices;
                updateGroupPlanPrice();
            }
        });
        gpPlus.addEventListener("click", () => {
            if (currentGpDevices < 10) {
                currentGpDevices++;
                gpDeviceCount.innerText = currentGpDevices;
                updateGroupPlanPrice();
            }
        });
    }
    
    if (groupPlanSelect) {
        groupPlanSelect.addEventListener("change", updateGroupPlanPrice);
    }
    
    if (btnGroupRequestSlot) {
        btnGroupRequestSlot.addEventListener("click", async () => {
            const planId = groupPlanSelect.value;
            if(!planId) {
                alert("Please select a plan.");
                return;
            }
            
            // Re-use the existing requestSlot logic but overriding the variables
            try {
                btnGroupRequestSlot.disabled = true;
                btnGroupRequestSlot.innerText = "Requesting...";
                
                const response = await fetch("/api/session/start/request/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": getCookie("csrftoken")
                    },
                    body: JSON.stringify({
                        mac_address: macAddress,
                        plan_id: parseInt(planId),
                        is_group_pass: true,
                        group_pass_devices: currentGpDevices,
                        group_pass_duration_minutes: 0 // Duration will be pulled from plan on backend
                    })
                });

                const data = await response.json();
                if (response.ok) {
                    groupPlanModal.style.display = "none";
                    startSessionBtn.disabled = true;
                    btnShowJoinGroup.disabled = true;
                    planCards.forEach(c => c.style.pointerEvents = "none");
                    startFlowMessage.style.display = "block";
                    startFlowMessage.className = "alert alert-warning";
                    startFlowMessage.innerHTML = `<strong>Insert coins now!</strong><br>Please insert exactly ₱${data.expected_amount}.`;
                    startFlowMeta.innerText = `Pending Amount: ₱${data.expected_amount}`;
                    
                    pollInterval = setInterval(checkCoinStatus, 2000);
                } else {
                    alert(data.error || "Failed to request coin slot.");
                    btnGroupRequestSlot.disabled = false;
                    btnGroupRequestSlot.innerHTML = `<i class="bi bi-hourglass-split"></i> Request Coin Slot`;
                }
            } catch (error) {
                console.error("Error requesting slot:", error);
                alert("Network error.");
                btnGroupRequestSlot.disabled = false;
                btnGroupRequestSlot.innerHTML = `<i class="bi bi-hourglass-split"></i> Request Coin Slot`;
            }
        });
    }

    // Join Group Modal logic
    const btnShowJoinGroup = document.getElementById("btn-show-join-group");
    const joinGroupModal = document.getElementById("joinGroupModal");
    const btnCancelJoin = document.getElementById("btn-cancel-join");
    const btnSubmitJoin = document.getElementById("btn-submit-join");
    const joinGroupCode = document.getElementById("join-group-code");
    const joinGroupError = document.getElementById("join-group-error");

    if (btnShowJoinGroup) {
        btnShowJoinGroup.addEventListener("click", () => {
            if(joinGroupModal) joinGroupModal.style.display = "flex";
        });
    }

    if (btnCancelJoin) {
        btnCancelJoin.addEventListener("click", () => {
            if(joinGroupModal) joinGroupModal.style.display = "none";
            joinGroupError.style.display = "none";
        });
    }

    if (btnSubmitJoin) {
        btnSubmitJoin.addEventListener("click", async () => {
            const code = joinGroupCode.value.trim().toUpperCase();
            if (!code || code.length !== 6) {
                joinGroupError.innerText = "Please enter a valid 6-character code.";
                joinGroupError.style.display = "block";
                return;
            }

            btnSubmitJoin.disabled = true;
            btnSubmitJoin.innerText = "Joining...";
            joinGroupError.style.display = "none";

            try {
                const response = await fetch("/api/session/join-group/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": getCookie("csrftoken")
                    },
                    body: JSON.stringify({
                        mac_address: macAddress,
                        group_code: code
                    })
                });

                const data = await response.json();
                if (response.ok) {
                    window.location.reload();
                } else {
                    joinGroupError.innerText = data.error || "Failed to join group.";
                    joinGroupError.style.display = "block";
                    btnSubmitJoin.disabled = false;
                    btnSubmitJoin.innerText = "Join";
                }
            } catch (err) {
                joinGroupError.innerText = "Network error. Please try again.";
                joinGroupError.style.display = "block";
                btnSubmitJoin.disabled = false;
                btnSubmitJoin.innerText = "Join";
            }
        });
    }
"""

# We'll just replace the old family pass listener with this block, since the old block might have "const familyPassCard" etc
# I'll just append this to the end of the file, and remove any family pass logic
code = re.sub(r'const groupPassToggle = document\.getElementById\("group-pass-toggle"\);.*?// Join Group Modal logic', '// Join Group Modal logic', code, flags=re.DOTALL)
code = re.sub(r'const groupPassModeRow = document\.getElementById\("group-pass-mode-row"\);.*?// Join Group Modal logic', '// Join Group Modal logic', code, flags=re.DOTALL)

# Let's just do a clean replacement of everything between `const requestSlotBtn = document.getElementById("request-slot-btn");` and `function updatePlanSelection() {` if we want to be safe. But appending is safer if we strip the previous `Join Group Modal logic`.
code = re.sub(r'// Join Group Modal logic.*$', new_js, code, flags=re.DOTALL)

with open("static/js/portal.js", "w", encoding="utf-8") as f:
    f.write(code)

print("static/js/portal.js rewritten.")
