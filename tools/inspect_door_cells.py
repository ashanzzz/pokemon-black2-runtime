import json
import urllib.request

url = "http://localhost:8765/api/v1/navigation/radar/slices?radius=4"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read().decode('utf-8'))

for s in data.get("slices", []):
    if s.get("floor_y") == 0:
        for row in s.get("grid", []):
            for cell in row:
                if cell.get("x") == 18 and cell.get("z") in (17, 18):
                    print(f"Cell ({cell.get('x')}, {cell.get('z')}): symbol={repr(cell.get('symbol'))}, kind={cell.get('kind')}, events={cell.get('events')}")
