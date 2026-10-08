import urllib.request, json

url = "http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=4"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read())

print("Top keys:", list(data.keys()))
for s in data.get("slices", []):
    print(f"Slice y={s.get('y')}: label={s.get('label')}, grid rows={len(s.get('grid', []))}, cols={len(s.get('grid', [[]])[0])}")
    cell = s.get('grid', [[]])[0][0]
    print(" Sample cell keys:", list(cell.keys()))
    print(" Sample cell:", cell)
