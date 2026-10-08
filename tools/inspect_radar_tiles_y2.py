import urllib.request, json

url = "http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=7"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read())

print("Tiles from radar/slices on Y=2:")
for s in data["slices"]:
    fy = s.get("floor_y", s.get("y"))
    if fy == 2:
        for row in s["grid"]:
            for c in row:
                if (c["z"] == 46 and 10 <= c["x"] <= 15) or (c["x"] == 15 and 44 <= c["z"] <= 46):
                    print(f"({c['x']}, {c['z']}, Y=2): sym={c.get('symbol')} tc={hex(c.get('tile_class',0))} kind={c.get('kind')} walkable={c.get('walkable')} mat={c.get('material',{}).get('kind')}")
