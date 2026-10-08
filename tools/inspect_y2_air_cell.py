import urllib.request, json, sys
sys.stdout.reconfigure(encoding="utf-8")

url = "http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=4"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read())

for s in data["slices"]:
    fy = s.get("floor_y", s.get("y"))
    if fy == 2:
        for row in s["grid"]:
            for c in row:
                if c.get("symbol") == "↕":
                    print(f"Slice Y=2 cell with ↕: ({c['x']}, {c['z']}):")
                    print("  symbol:", c.get("symbol"))
                    print("  kind:", c.get("kind"))
                    print("  tile_class:", c.get("tile_class"))
                    print("  material:", c.get("material"))
                    print("  status:", c.get("status"))
                    print("  alternate_layer_y:", c.get("alternate_layer_y"))
                    break
            else:
                continue
            break
