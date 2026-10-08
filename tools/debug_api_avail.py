import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

req = urllib.request.Request("http://127.0.0.1:8765/api/v1/player/fly/available")
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode("utf-8"))
print("Type of res:", type(res), "Length:", len(res))
if res:
    print("First item:", res[0])