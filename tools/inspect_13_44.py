import urllib.request, json

url = "http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=7"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read().decode("utf-8"))

for s in data["slices"]:
    fy = s.get("floor_y", s.get("y"))
    for row in s["grid"]:
        for c in row:
            if c["x"] == 13 and c["z"] == 44:
                print(f"Slice Y={fy}: cell=({c['x']}, {c['z']}, Y={c['y']}) sym={c.get('symbol')} walkable={c.get('walkable')} kind={c.get('kind')} tile_class={c.get('tile_class')}")
