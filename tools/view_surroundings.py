# -*- coding: utf-8 -*-
"""Inspect and display the 5 tiles in Up, Down, Left, and Right directions from the player."""
import urllib.request
import json
import sys

import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "black2-inspector/1.0"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))

def classify_terrain(tclass, flags, blocked, walkable):
    """Translate raw Gen-5 ROM TileClass & flags into clear human-readable semantics."""
    if tclass == 4:
        kind = "深色草丛 (Tall Grass, 野生宝可梦遇敌区)"
    elif tclass == 31:
        kind = "平坦草坪 (Regular Lawn, 干净安全地面)"
    elif tclass in (1, 2, 3):
        if blocked:
            kind = "围栏边界/篱笆障碍 (Fence Border / Obstacle)"
        else:
            kind = "普通泥土路 (Dirt Path, 泥地通道)"
    elif tclass == 114:
        kind = "木制围栏/栅栏 (Wooden Railing / Fence)"
    elif tclass == 60:
        kind = "水域水面 (Water Surface, 需要冲浪)"
    elif tclass == 128:
        kind = "建筑墙体/岩壁 (Solid Wall / Cliff)"
    else:
        kind = f"特殊地形地块 (Class {tclass})"

    if blocked or not walkable:
        status = "❌ 障碍物阻挡 (Blocked)"
    else:
        status = "✅ 可自由通行 (Walkable)"

    return kind, status

def main():
    try:
        player_data = fetch_json("http://127.0.0.1:8765/api/v1/player/runtime")
    except Exception as e:
        print(f"❌ 无法连接到游戏后台 API (127.0.0.1:8765): {e}")
        return

    pos = player_data.get("position", {}).get("grid", {})
    px, py, pz = pos.get("x"), pos.get("y", 2), pos.get("z")
    zone_id = player_data.get("zone_id", 445)
    orient = player_data.get("orientation", {})
    facing = orient.get("facing", "North")
    facing_zh = orient.get("facing_zh", "北")

    facing_arrows = {"North": "↑ (朝北/上)", "South": "↓ (朝南/下)", "West": "← (朝西/左)", "East": "→ (朝东/右)"}
    arrow_str = facing_arrows.get(facing, facing)

    print("\n" + "=" * 68)
    print(f"        🧭 主角四周空间环境雷达 (上下左右各 5 单元格) 🧭")
    print("=" * 68)
    print(f"当前位置: 算木牧场 (Zone {zone_id}) | 坐标: (X={px}, Y={py}, Z={pz})")
    print(f"面对方向: {arrow_str} | 移动状态: 站立待命 (可自由移动)")
    print("-" * 68)

    # Load static provider for fast batch surface lookup
    from backend.black2.api.navigation_routes import navigation_static_provider
    provider = navigation_static_provider()

    # Query tile helper
    def get_tile(x, z, y):
        surf = provider.surface_at(zone_id, x, z, y, allow_unverified_terrain=True)
        if not surf or not surf.get("surfaces"):
            return {
                "walkable": False, "tclass": None, "flags": None,
                "kind": "虚空/地图外边界 (Out of Bounds)", "status": "❌ 不可通行"
            }
        top = surf["surfaces"][-1]
        tclass = top.get("tile_class")
        flags = top.get("flags")
        blocked = top.get("static_blocked")
        walkable = surf.get("walkable", False)
        kind, status = classify_terrain(tclass, flags, blocked, walkable)
        return {"walkable": walkable and not blocked, "tclass": tclass, "flags": flags, "kind": kind, "status": status}

    # Inspect current tile
    cur = get_tile(px, pz, py)
    print(f"【当前脚下】 (X={px}, Z={pz}): {cur['status']} | {cur['kind']} (Class={cur['tclass']}, Flags={cur['flags']})")
    print("-" * 68)

    # 4 Cardinal directions: 5 steps each
    directions = [
        ("【上 / 北方 (North)】 (Z 递减，面对方向)" if facing == "North" else "【上 / 北方 (North)】 (Z 递减)", [(px, pz - step) for step in range(1, 6)]),
        ("【下 / 南方 (South)】 (Z 递增，后背方向)" if facing == "North" else "【下 / 南方 (South)】 (Z 递增)", [(px, pz + step) for step in range(1, 6)]),
        ("【左 / 西方 (West)】 (X 递减，左手边)" if facing == "North" else "【左 / 西方 (West)】 (X 递减)", [(px - step, pz) for step in range(1, 6)]),
        ("【右 / 东方 (East)】 (X 递增，右手边)" if facing == "North" else "【右 / 东方 (East)】 (X 递增)", [(px + step, pz) for step in range(1, 6)])
    ]

    for dir_title, coords in directions:
        print(f"\n{dir_title}:")
        for step, (tx, tz) in enumerate(coords, 1):
            info = get_tile(tx, tz, py)
            print(f"  [{step}格] 坐标 (X={tx}, Z={tz}): {info['status']:<18} | {info['kind']} (Class={info['tclass']})")

    # ASCII Mini Map (11 x 11, radius 5)
    print("\n" + "-" * 68)
    print("【四周 11×11 局部俯视地形微缩雷达图】")
    print("图例: [P]=主角所在  [*]=深色草丛(遇敌区)  [.]=平坦草坪/道路  [#]=围栏障碍物")
    print("         ↑ 北 (Z-)")
    print("    " + "".join(f"{x:3d}" for x in range(px - 5, px + 6)))
    for tz in range(pz - 5, pz + 6):
        row_chars = []
        for tx in range(px - 5, px + 6):
            if tx == px and tz == pz:
                char = " P "
            else:
                t = get_tile(tx, tz, py)
                if not t["walkable"]:
                    char = " # "
                elif t["tclass"] == 4:
                    char = " * "
                else:
                    char = " . "
            row_chars.append(char)
        print(f"{tz:3d} " + "".join(row_chars))
    print("         ↓ 南 (Z+)")
    print("=" * 68 + "\n")

if __name__ == "__main__":
    main()
