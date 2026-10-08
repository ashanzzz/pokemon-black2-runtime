import urllib.request, json

url = "http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=7"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read())

for s in data["slices"]:
    fy = s.get("floor_y", s.get("y"))
    if fy == 2:
        for row in s["grid"]:
            for c in row:
                if c["x"] == 16 and c["z"] == 44:
                    print("Cell at (16, 44, Y=2):")
                    for k in ['x', 'z', 'y', 'symbol', 'kind', 'walkable', 'tile_class', 'tile_class_hex', 'material', 'catwalk']:
                        print(f"  {k}:", c.get(k))
