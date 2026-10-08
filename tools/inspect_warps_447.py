import urllib.request, json

def get(url):
    with urllib.request.urlopen("http://localhost:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

for zid in [446, 447, 448]:
    w = get(f"/api/v1/ai/map/warps?zone_id={zid}")
    print(f"=== ZONE {zid} WARPS ({len(w.get('warps', []))}) ===")
    for item in w.get("warps", []):
        src = item["source"]["tile"]
        dst = item["destination"]
        print(f"  ({src['x']}, {src['z']}) -> Zone {dst.get('zone_id')} ({dst.get('label', {}).get('display_name')})")
