import re

with open("static/js/portal.js", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Export applyCoinRequestState and startPolling to window inside initProductionStartFlow
old_init_block = """    const startPolling = () => {
        clearPolling();
        state.pollTimer = setInterval(pollRequestStatus, 3000);
        pollRequestStatus();
    };"""

new_init_block = """    const startPolling = () => {
        clearPolling();
        state.pollTimer = setInterval(pollRequestStatus, 3000);
        pollRequestStatus();
    };
    
    // EXPORT TO GLOBAL SCOPE FOR GROUP PLAN FLOW
    window.applyCoinRequestState = applyCoinRequestState;
    window.startPolling = startPolling;"""

if old_init_block in code:
    code = code.replace(old_init_block, new_init_block, 1)

# 2. Revert window.location.reload() back to calling the window functions
old_group_block = """                if(startFlowMeta) {
                    startFlowMeta.innerText = `Pending Amount: ₱${data.coin_request.expected_amount}`;
                }
                
                // Reload the page to automatically start polling the new coin request
                window.location.reload();
                
            } else {"""

new_group_block = """                if(startFlowMeta) {
                    startFlowMeta.innerText = `Pending Amount: ₱${data.coin_request.expected_amount}`;
                }
                
                if (window.applyCoinRequestState && window.startPolling) {
                    window.applyCoinRequestState(data.coin_request);
                    window.startPolling();
                } else {
                    window.location.reload(); // Fallback
                }
                
            } else {"""

if old_group_block in code:
    code = code.replace(old_group_block, new_group_block, 1)

with open("static/js/portal.js", "w", encoding="utf-8") as f:
    f.write(code)

print("portal.js patched successfully.")
