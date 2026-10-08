import urllib.request, json, sys
sys.stdout.reconfigure(encoding="utf-8")

url = "http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=4"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read().decode("utf-8"))

for s in data["slices"]:
    fy = s.get("floor_y", s.get("y"))
    grid = s.get("grid", [])
    walkable_count = sum(1 for row in grid for c in row if c.get("walkable"))
    catwalk_count = sum(1 for row in grid for c in row if c.get("symbol") in ("╫", "╪"))
    print(f"Slice Y={fy}: walkable={walkable_count}, catwalk={catwalk_count}")
    for row in grid:
        for c in row:
            if c.get("symbol") in ("╫", "╪"):
                print(f"  Catwalk tile at ({c['x']}, {c['z']}, Y={c['y']}) sym={c.get('symbol')} walkable={c.get('walkable')} kind={c.get('kind')}")
