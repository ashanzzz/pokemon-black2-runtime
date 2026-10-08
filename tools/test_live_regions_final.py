import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

req = urllib.request.Request("http://127.0.0.1:8765/api/v1/player/fly/regions")
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode("utf-8"))

print("=== Live /api/v1/player/fly/regions Summary ===")
print("Format:", res.get("format"))
print("Status:", res.get("status"))
print("Total Destinations:", res.get("total_destinations"))
print("Current Flight Status:", json.dumps(res.get("current_flight_status"), ensure_ascii=False, indent=2))
print("Categories Summary:", json.dumps(res.get("categories_summary"), ensure_ascii=False, indent=2))
print("Sample Destinations:")
for d in res.get("destinations", [])[:6]:
    print(f"  Zone {d['zone_id']:3d}: {d['name_zh']} ({d['name_en']}) | 分类: {d['category_zh']} | 中心: {d['has_pokemon_center']} | 道馆: {d['has_gym']} | 门垫: {d['landing_grid']}")