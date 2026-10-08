import json, urllib.request

url = "http://localhost:8765/api/v1/navigation/radar/slices?radius=4"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read().decode('utf-8'))

print("Total slices:", len(data.get("slices", [])))
print("Player floor_y:", data.get("player_floor_y"))
print("Center:", data.get("center"))

for s in data.get("slices", []):
    fy = s.get("floor_y")
    print(f"\n--- Slice Floor Y={fy} ---")
    grid = s.get("grid", [])
    for row in grid:
        z = row[0]["z"]
        symbols = "".join(c.get("symbol", " ") for c in row)
        row_str = f"Z={z:2d}: " + " ".join(f"{c.get('symbol'):>2}" for c in row)
        if any(c.get("symbol") == "D" for c in row):
            row_str += " <--- HAS D!"
        if any(c.get("symbol") == "P" for c in row):
            row_str += " <--- HAS P!"
        print(row_str)
