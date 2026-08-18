import re

with open("static/js/portal.js", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Add fields to state
old_state = """    const state = {
        requestId: null,
        planId: null,
        readyToStart: false,
        pollTimer: null,
        pollInFlight: false,
    };"""

new_state = """    const state = {
        requestId: null,
        planId: null,
        readyToStart: false,
        pollTimer: null,
        pollInFlight: false,
        isGroupPass: false,
        groupDevices: null,
    };"""

if old_state in code:
    code = code.replace(old_state, new_state, 1)

# 2. Update state in applyCoinRequestState
old_apply = """    const applyCoinRequestState = (coinRequest) => {
        state.requestId = coinRequest ? coinRequest.id : null;
        state.readyToStart = Boolean(coinRequest && coinRequest.ready_to_start);"""

new_apply = """    const applyCoinRequestState = (coinRequest) => {
        state.requestId = coinRequest ? coinRequest.id : null;
        state.readyToStart = Boolean(coinRequest && coinRequest.ready_to_start);
        if (coinRequest) {
            state.isGroupPass = coinRequest.is_group_pass || false;
            state.groupDevices = coinRequest.group_pass_devices || null;
            if (coinRequest.plan_id) {
                state.planId = coinRequest.plan_id;
            }
        }"""

if old_apply in code:
    code = code.replace(old_apply, new_apply, 1)

# 3. Use state in startBtn.addEventListener
old_start = """    startBtn.addEventListener("click", async () => {
        const planId = selectedPlanId();
        if (!planId) {
            setStartFlowMessage("Select a plan first.", "warning");
            startBtn.disabled = true;
            startBtn.dataset.readyToStart = "0";
            return;
        }

        if (!state.readyToStart) {
            setStartFlowMessage("Insert enough coins first, then tap Connect Now.", "warning");
            return;
        }

        requestBtn.disabled = true;
        startBtn.disabled = true;

        try {
            const response = await fetch("/api/session/start/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken()
                },
                body: JSON.stringify({ mac_address: macAddress, plan_id: planId }),
            });"""

new_start = """    startBtn.addEventListener("click", async () => {
        const planId = state.planId || selectedPlanId();
        if (!planId) {
            setStartFlowMessage("Select a plan first.", "warning");
            startBtn.disabled = true;
            startBtn.dataset.readyToStart = "0";
            return;
        }

        if (!state.readyToStart) {
            setStartFlowMessage("Insert enough coins first, then tap Connect Now.", "warning");
            return;
        }

        requestBtn.disabled = true;
        startBtn.disabled = true;

        try {
            const payload = { mac_address: macAddress, plan_id: planId };
            if (state.isGroupPass) {
                payload.is_group_pass = true;
                payload.group_pass_devices = state.groupDevices;
            }

            const response = await fetch("/api/session/start/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken()
                },
                body: JSON.stringify(payload),
            });"""

if old_start in code:
    code = code.replace(old_start, new_start, 1)
else:
    print("WARNING: start block not found")

with open("static/js/portal.js", "w", encoding="utf-8") as f:
    f.write(code)

print("portal.js patched.")
