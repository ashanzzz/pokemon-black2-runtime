import sys, json, urllib.request, urllib.parse
sys.stdout.reconfigure(encoding="utf-8")

q = urllib.parse.quote("双龙")
url = f"http://127.0.0.1:8765/api/v1/player/fly/destinations?search={q}&format=flat"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode("utf-8"))

print(f"Search returned {len(res)} results:")
for r in res:
    print(f"  Zone {r['zone_id']}: {r['name_zh']} ({r['name_en']}) | {r['category_zh']} | Center: {r['has_pokemon_center']}")