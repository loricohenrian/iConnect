import requests

# We need the mac_address of the user to test this?
# No, let's just create a completely dummy one. We can just POST to localhost.
payload = {
    "mac_address": "AA:BB:CC:DD:EE:12",
    "plan_id": 1,
    "is_group_pass": True,
    "group_pass_devices": 5
}
try:
    resp = requests.post("http://localhost:8000/api/session/start/", json=payload)
    print("Status:", resp.status_code)
    print("Text:", resp.text)
except Exception as e:
    print(e)
