import urllib.request, json, sys
sys.stdout.reconfigure(encoding="utf-8")

url = "http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=7"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read())

print("Inspecting stair tiles (12, 46) and (11, 46) across slices:")
for s in data["slices"]:
    fy = s.get("floor_y", s.get("y"))
    for row in s["grid"]:
        for c in row:
            if c["z"] == 46 and c["x"] in (11, 12):
                print(f"Slice Y={fy}: ({c['x']}, {c['z']}) sym={c.get('symbol')} kind={c.get('kind')} walkable={c.get('walkable')} status={c.get('status')}")
