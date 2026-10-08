import urllib.request, json, sys
sys.stdout.reconfigure(encoding="utf-8")
body = json.dumps({"service": "nearest", "movement_mode": "auto"}).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:8765/api/v1/agent/automation/recovery", data=body, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        print("Response Code:", resp.status)
        print("Response Body:", res)
except urllib.error.HTTPError as e:
    print("HTTP Error:", e.code, e.read().decode("utf-8"))