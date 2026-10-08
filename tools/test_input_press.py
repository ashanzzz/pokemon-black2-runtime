import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

body = json.dumps({"button": "B", "frames": 4}).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:8765/api/v1/input/press", data=body, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode("utf-8"))
print("Input Press response:", res)