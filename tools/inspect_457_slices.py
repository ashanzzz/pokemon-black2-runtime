import urllib.request, json

url = "http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=4"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read().decode("utf-8"))

print("Zone:", data.get("zone_id"), data.get("zone_name"))
print("Player floor y:", data.get("player_floor_y"))
print("Active layers:", data.get("active_layers"))
for s in data.get("slices", []):
    fy = s.get("floor_y", s.get("y"))
    grid = s.get("grid", [])
    walkable_count = sum(1 for row in grid for c in row if c.get("walkable"))
    catwalk_count = sum(1 for row in grid for c in row if c.get("symbol") in ("╫", "╪"))
    symbols = set(c.get("symbol") for row in grid for c in row)
    print(f"Slice y={fy}: label={s.get('label')}, walkable={walkable_count}, catwalk={catwalk_count}, symbols={symbols}")
