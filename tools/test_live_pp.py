import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

req = urllib.request.Request("http://127.0.0.1:8765/api/v1/battle/moves?actor=player:0")
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode("utf-8"))

print("=== 实时技能 PP 管理与可用性检测 (GET /api/v1/battle/moves) ===")
print("全招式可用状态:", data.get("has_usable_moves"))
print("全招式PP枯竭状态:", data.get("all_pp_exhausted"))
print("\n当前招式明细:")
for m in data.get("moves", []):
    status_str = f"可用 (PP: {m['current_pp']}/{m['max_pp']})" if m['usable'] else f"枯竭 (PP: 0/{m['max_pp']})"
    print(f"  槽位 {m['slot']}: 【{m['name']}】({m['type']}) | {status_str} | 威力: {m.get('power')} | 命中: {m.get('accuracy')}%")