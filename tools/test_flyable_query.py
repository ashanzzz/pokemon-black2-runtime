import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

req = urllib.request.Request("http://127.0.0.1:8765/api/v1/player/fly/destinations?status=flyable&format=flat")
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode("utf-8"))

print("Count with ?status=flyable:", len(res))