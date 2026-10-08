import urllib.request, json, sys
sys.stdout.reconfigure(encoding="utf-8")

url = "http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=7"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read())

print("Inspecting (10, 46) across slices:")
for s in data["slices"]:
    fy = s.get("floor_y", s.get("y"))
    for row in s["grid"]:
        for c in row:
            if c["x"] == 10 and c["z"] == 46:
                print(f"Slice Y={fy}:")
                for k in ["x", "z", "y", "symbol", "kind", "walkable", "status", "tile_class", "material", "alternate_layer_available", "alternate_layer_y"]:
                    print(f"  {k}:", c.get(k))
