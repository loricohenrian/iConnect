import requests

try:
    response = requests.post("http://127.0.0.1:8000/api/session/start/request/", json={
        "mac_address": "AA:BB:CC:DD:EE:FF",
        "plan_id": 1,
        "is_group_pass": True,
        "group_pass_devices": 2
    })
    print(response.status_code)
    print(response.text)
except Exception as e:
    print(e)
