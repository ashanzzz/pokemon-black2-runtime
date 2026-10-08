import urllib.request, json

def get(url):
    with urllib.request.urlopen("http://localhost:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

p = get("/api/v1/player/runtime")
print("Zone:", p.get("zone_id"))
print("Grid:", p.get("position", {}).get("grid"))
print("World:", p.get("position", {}).get("world"))
print("Facing:", p.get("orientation", {}).get("facing"))
print("Semantic State:", p.get("locomotion", {}).get("semantic_state"))
print("Transport Mode:", p.get("locomotion", {}).get("transport_mode"))

cap = get("/api/v1/player/capabilities")
print("Legal Caps:", cap.get("legal_capabilities"))
print("Reasons:", cap.get("reasons"))
