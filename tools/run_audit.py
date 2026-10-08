import sys
sys.path.insert(0, '.')
import requests, json

print("==========================================================================")
print("   LIVE AUDIT: 真实剧情阻挡点、精确封锁坐标与 A* 决策闭环测试               ")
print("==========================================================================")

# 1. 真实回读双龙市 (Zone 120) 当前真实生效的剧情拦截防线
r120 = requests.get("http://127.0.0.1:8765/api/v1/navigation/story-roadblocks?zone_id=120").json()
print(f"\n【现场 1】：双龙市 (Zone 120) 海底隧道 / 东出城防线")
print(f"  - 阻挡状态: {r120.get('active_roadblocks_count')} 条主线拦截防线【实时生效中！】")
for rb in r120.get("active_roadblocks", []):
    print(f"  - 截停触发脚本: SCRID #{rb['script_id']}")
    print(f"  - 内存主线变量: {rb['var_id']} (实时内存值={rb['live_value']} == 预期触发值={rb['expected_value']})")
    print(f"  - 封锁的具体坐标清单 (共 {len(rb['blocked_tiles'])} 格跨街大路):")
    for idx, t in enumerate(rb['blocked_tiles'], 1):
        print(f"      {idx}. (X={t['x']}, Z={t['z']}, Y={t['y']}) [踩入即强制截停退回]")

# 2. 真实回读海边洞穴 (Zone 465) 岩殿居蟹剧情拦截防线
r465 = requests.get("http://127.0.0.1:8765/api/v1/navigation/story-roadblocks?zone_id=465").json()
print(f"\n【现场 2】：海边洞穴 (Zone 465) 青海波市方向岩殿居蟹拦截")
print(f"  - 阻挡状态: {r465.get('active_roadblocks_count')} 条主线拦截防线【实时生效中！】")
for rb in r465.get("active_roadblocks", []):
    print(f"  - 截停触发脚本: SCRID #{rb['script_id']}")
    print(f"  - 内存主线变量: {rb['var_id']} (实时内存值={rb['live_value']} == 预期触发值={rb['expected_value']})")
    print(f"  - 封锁的具体坐标清单:")
    for idx, t in enumerate(rb['blocked_tiles'], 1):
        print(f"      {idx}. (X={t['x']}, Z={t['z']}, Y={t['y']})")

# 3. 雷达切片回读验证：验证雷达对该阻挡坐标 (440, 175, Y=0) 的硬阻隔与 '!' 符号
print(f"\n【现场 3】：雷达切片回读双龙市被封锁地块 (440, 175, Y=0)")
radar_slice = requests.get("http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=2&zone_id=120&x=440&z=175&y=0").json()
found_blocked_cell = None
for s in radar_slice.get("slices", []):
    if s.get("floor_y") == 0:
        for row in s.get("grid", []):
            for c in row:
                if c.get("x") == 440 and c.get("z") == 175:
                    found_blocked_cell = c
                    break

if found_blocked_cell:
    print(f"  - 瓦片符号: {found_blocked_cell.get('symbol')} (预期: '!' 剧情拦截线)")
    print(f"  - 通行状态: walkable={found_blocked_cell.get('walkable')} (预期: False 绝对不可通行)")
    print(f"  - 语义类型: {found_blocked_cell.get('kind')}")
    print(f"  - 是否剧情截停: {found_blocked_cell.get('is_story_gate_trigger')}")
else:
    print("  - 未在切片中定位到地块")

# 4. 状态转移测试：模拟剧情推进 (Var 0x40D5 推进至 1) 验证动态解封
print(f"\n【现场 4】：动态解封状态转移测试 (验证剧情推进后防线自动消失)")
from backend.black2.world.gen5_rom_map import Gen5RomMap
from backend.black2.api.navigation_routes import _scan_active_story_triggers, navigation_static_provider

prov = navigation_static_provider()
mock_works = [0] * 431
mock_works[0x40D5 - 0x4000] = 1  # 模拟击败夏卡、拯救双龙市，剧情推进 Var 0x40D5 = 1

trig_map_adv, rbs_adv, impass_adv = _scan_active_story_triggers(prov, 120, works_u16=mock_works)
print(f"  - 剧情推进后激活防线数量: {len(rbs_adv)} (预期: 0 已解封放行)")
print(f"  - 坐标 (440, 175) 处是否存在阻隔: {(440, 175) in trig_map_adv} (预期: False 完全放行)")
print("==========================================================================")
