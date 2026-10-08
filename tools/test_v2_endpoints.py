import urllib.request
import json

endpoints = [
    "/api/v1/player/runtime",
    "/api/v1/navigation/radar/slices?radius=4",
    "/api/v1/progression/state",
    "/api/v1/game/party",
    "/api/v1/game/inventory",
    "/api/v1/battle/decisions",
    "/api/v1/agent/story/plan"
]

base = "http://localhost:8765"
all_ok = True
for ep in endpoints:
    url = base + ep
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            print(f"[OK 200] {ep}")
    except Exception as e:
        print(f"[FAIL] {ep}: {e}")
        all_ok = False

if all_ok:
    print("\nALL V2 FRONTEND ENDPOINTS ARE FULLY OPERATIONAL!")
else:
    print("\nSome endpoints failed.")
