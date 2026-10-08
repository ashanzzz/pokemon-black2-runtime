import urllib.request, json

def get(url):
    with urllib.request.urlopen("http://localhost:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

actors = get("/api/v1/map/v6/actors/live")
for a in actors.get("actors", []):
    g = a.get("grid") or {}
    print(f"Slot {a.get('slot')}: model={a.get('model_id')} pos=({g.get('x')}, {g.get('z')}) facing={a.get('facing')}")
