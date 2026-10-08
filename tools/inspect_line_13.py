import urllib.request, json, sys
sys.stdout.reconfigure(encoding="utf-8")

url = "http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=7"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read())

for z in range(43, 49):
    for s in data["slices"]:
        fy = s.get("floor_y", s.get("y"))
        for row in s["grid"]:
            for c in row:
                if c["x"] == 13 and c["z"] == z:
                    print(f"Z={z}, Y={fy}: sym={c.get('symbol')} walkable={c.get('walkable')} kind={c.get('kind')}")
