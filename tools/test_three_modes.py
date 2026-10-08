import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

def get(url):
    req = urllib.request.Request("http://127.0.0.1:8765" + url)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

# 1. Total overview
regions = get("/api/v1/player/fly/regions")
print("=== 1. 全部飞行地区总览 ===")
print("总计目标数:", regions.get("total_destinations"))
print("当前已开放/可飞数:", regions.get("unlocked_destinations_count"))
print("当前未开放/锁定数:", regions.get("locked_destinations_count"))
print("当前主角所在区域:", regions.get("current_flight_status", {}).get("current_zone_name"))

# 2. Available / Flyable
avail = get("/api/v1/player/fly/available")
print(f"\n=== 2. 可飞行地区列表 (共 {len(avail)} 个) ===")
for a in avail[:8]:
    pc = "有中心" if a.get("has_pokemon_center") else "无中心"
    print(f"  Zone {a['zone_id']:3d}: {a['name_zh']} ({a['name_en']}) | {a['category_zh']} | {pc} | 门垫 {a['landing_grid']}")

# 3. Locked / Non-flyable
locked = get("/api/v1/player/fly/locked")
print(f"\n=== 3. 未开放/不可飞行地区列表 (共 {len(locked)} 个) ===")
for l in locked[:8]:
    print(f"  Zone {l['zone_id']:3d}: {l['name_zh']} ({l['name_en']}) | 所需徽章: {l['required_badge_count']} | 原因: {l['lock_reason']}")