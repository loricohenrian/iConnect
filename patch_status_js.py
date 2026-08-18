import re

with open("static/js/portal.js", "r", encoding="utf-8") as f:
    code = f.read()

# The fetchSessionStatus polling updates sessionTimer
# Let's see if we can find where `fetchSessionStatus` handles the response
old_status_check = """        } catch (error) {
            console.error("Status check failed", error);
        }
    }

    if (sessionTimer) {
        setInterval(fetchSessionStatus, 30000); // Check every 30s"""

new_status_check = """            // UPDATE GROUP PLAN STATUS IF PRESENT
            if (data.group_connected !== undefined && data.group_max !== undefined) {
                const groupPlanStatus = document.getElementById("group-plan-status");
                if (groupPlanStatus) {
                    groupPlanStatus.innerText = `Group Plan: ${data.group_connected} / ${data.group_max} devices connected`;
                }
            }

        } catch (error) {
            console.error("Status check failed", error);
        }
    }

    if (sessionTimer) {
        setInterval(fetchSessionStatus, 15000); // Check every 15s to make it more live"""

if old_status_check in code:
    code = code.replace(old_status_check, new_status_check)
else:
    # If not exactly matching, let's just do regex
    code = re.sub(
        r'(\}\s*catch\s*\(error\)\s*\{\s*console\.error\("Status check failed", error\);\s*\})',
        r'if (data && data.group_connected !== undefined && data.group_max !== undefined) { const groupPlanStatus = document.getElementById("group-plan-status"); if (groupPlanStatus) { groupPlanStatus.innerText = `Group Plan: ${data.group_connected} / ${data.group_max} devices connected`; } } \1',
        code
    )


with open("static/js/portal.js", "w", encoding="utf-8") as f:
    f.write(code)

print("static/js/portal.js status check patched.")
