import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

def get(path):
    req = urllib.request.Request("http://127.0.0.1:8765" + path)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

ident = get("/api/v1/battle/identity")
opp = ident.get("opponent", {}).get("active", {})
pl = ident.get("player", {}).get("active", {})

print("=== 实时物理 RAM 敌我双方真实档案 (btl_pokeparam.c 解码) ===")
print(f"敌方出战: {opp.get('species', {}).get('names', {}).get('zh-Hans')} (物种ID: {opp.get('species_id')}) 等级: Lv.{opp.get('level')} HP: {opp.get('current_hp')}/{opp.get('max_hp')}")
print(f"敌方特性: {opp.get('ability', {}).get('name')}")
print(f"敌方五维能力: {opp.get('stats')}")
print(f"敌方招式槽: {[m.get('name') for m in opp.get('moves', [])]}")

print(f"\n我方出战: {pl.get('species', {}).get('names', {}).get('zh-Hans')} (物种ID: {pl.get('species_id')}) 等级: Lv.{pl.get('level')} HP: {pl.get('current_hp')}/{pl.get('max_hp')}")
print(f"我方特性: {pl.get('ability', {}).get('name')}")
print(f"我方五维能力: {pl.get('stats')}")

dec = get("/api/v1/battle/decisions")
print(f"\n=== 技能属性克制评估与 AI 决策推荐 (Battle Decision Engine) ===")
for m in dec.get("decision", {}).get("moves_evaluated", []):
    print(f"  槽位 {m.get('slot')}: 【{m.get('name_zh')}】(属性: {m.get('move_type_name')}) | 基础威力: {m.get('base_power')} | 命中率: {m.get('accuracy')}% | 克制倍率: {m.get('type_multiplier')}x | 预估评分: {m.get('expected_score')} | 裁决: {m.get('verdict')}")

print(f"\n最优推荐招式: {dec.get('decision', {}).get('best_move', {}).get('name_zh')}")
print(f"推荐下发动作: {dec.get('recommended_action')}")