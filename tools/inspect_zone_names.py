import urllib.request, json

def get(url):
    with urllib.request.urlopen("http://localhost:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

try:
    doors = get("/api/v1/ai/map/doors?zone_id=448")
    print("=== ZONE 448 DOORS ===")
    for d in doors.get("doors", []):
        print(d.get("door_id"), d.get("source", {}).get("tile"), "->", d.get("target_zone_id"), d.get("destination_label", {}).get("display_name"))
except Exception as e:
    print("doors error:", e)

for zid in [446, 447, 448, 449, 450, 451, 452, 453, 454, 455, 456, 457]:
    try:
        zinfo = get(f"/api/v1/ai/map/zone/{zid}")
        print(f"Zone {zid}: {zinfo.get('name_zh')} / {zinfo.get('display_name')} (env={zinfo.get('environment')})")
    except Exception as e:
        print(f"Zone {zid}: error {e}")
