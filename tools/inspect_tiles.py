import sys, os
sys.path.insert(0, os.getcwd())
import urllib.request, json

def get(url):
    with urllib.request.urlopen("http://localhost:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

player = get("/api/v1/player/runtime")
print("Zone:", player.get("zone_id"))
print("Grid:", player.get("position", {}).get("grid"))

# Check warps from api/v1/ai/map/warps
try:
    warps = get("/api/v1/ai/map/warps")
    print("Warps:", json.dumps(warps, indent=2))
except Exception as e:
    print("Warps err:", e)
