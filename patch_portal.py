import re

with open("static/js/portal.js", "r", encoding="utf-8") as f:
    code = f.read()

old_block = """                if(startFlowMeta) {
                    startFlowMeta.innerText = `Pending Amount: ₱${data.coin_request.expected_amount}`;
                }
                
                // Manually start polling since we bypassed the normal click
                initProductionStartFlow(getMacAddress()); 
                // Or rather we should just set the coin request state
                applyCoinRequestState(data.coin_request);
                startPolling();
                
            } else {"""

new_block = """                if(startFlowMeta) {
                    startFlowMeta.innerText = `Pending Amount: ₱${data.coin_request.expected_amount}`;
                }
                
                // Reload the page to automatically start polling the new coin request
                window.location.reload();
                
            } else {"""

code = code.replace(old_block, new_block)

with open("static/js/portal.js", "w", encoding="utf-8") as f:
    f.write(code)

print("portal.js patched.")
