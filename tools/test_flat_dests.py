import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

req = urllib.request.Request("http://127.0.0.1:8765/api/v1/player/fly/destinations?format=flat")
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode("utf-8"))

print("Total dests returned:", len(res))
if res:
    print("Statuses:", set(r.get("status") for r in res))
    print("Flyables:", set(r.get("flyable") for r in res))
    print("Req badges sample:", [r.get("required_badge_count") for r in res[:5]])