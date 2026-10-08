import urllib.request, json

def get(url):
    with urllib.request.urlopen("http://localhost:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

radar = get("/api/v1/navigation/radar/grid?zone_id=448&radius=8")
print("=== RADAR ASCII MAP ===")
print(radar.get("ascii_map"))
print("Legend:", radar.get("legend"))
