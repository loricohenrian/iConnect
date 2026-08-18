import re

with open("static/js/portal.js", "r", encoding="utf-8") as f:
    code = f.read()

old_poll = """function pollSessionStatus(macAddress, intervalMs = 3000) {
    setInterval(async () => {
        try {
            const response = await fetch(
                `/api/session/status/?mac_address=${encodeURIComponent(macAddress)}`
            );
            const data = await response.json();

            if (data.status === "expired") {
                window.location.href = buildPortalUrl("/", macAddress, { expired: 1 });
            }
        } catch (error) {
            console.error("Status poll error:", error);
        }
    }, intervalMs);
}"""

new_poll = """function pollSessionStatus(macAddress, intervalMs = 3000) {
    setInterval(async () => {
        try {
            const response = await fetch(
                `/api/session/status/?mac_address=${encodeURIComponent(macAddress)}`
            );
            const data = await response.json();

            if (data.status === "expired") {
                window.location.href = buildPortalUrl("/", macAddress, { expired: 1 });
            }
            
            if (data.group_max && data.group_connected) {
                const groupStatusEl = document.getElementById("group-plan-status");
                if (groupStatusEl) {
                    groupStatusEl.innerText = `Group Plan: ${data.group_connected} / ${data.group_max} devices connected`;
                }
            }
        } catch (error) {
            console.error("Status poll error:", error);
        }
    }, intervalMs);
}"""

code = code.replace(old_poll, new_poll)

with open("static/js/portal.js", "w", encoding="utf-8") as f:
    f.write(code)

print("pollSessionStatus patched")
