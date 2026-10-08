# -*- coding: utf-8 -*-
"""Command-line Inspector for Current Pokémon Battle State."""
import urllib.request
import json
import sys

# Force UTF-8 stdout if supported
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def main():
    try:
        url = "http://127.0.0.1:8765/api/v1/battle/identity"
        res = json.loads(urllib.request.urlopen(url, timeout=5).read().decode("utf-8"))
    except Exception as e:
        print(f"无法连接到游戏后台 API (127.0.0.1:8765): {e}")
        return

    active = res.get("status") == "candidate" or res.get("presence", {}).get("active") is True
    if not active:
        print("当前没有正在进行的对战。")
        return

    p = res.get("player", {}).get("active") or {}
    o = res.get("opponent", {}).get("active") or {}

    print("\n" + "=" * 58)
    print("        [ POKEMON BLACK 2 对战实时内存数据 ]")
    print("=" * 58)

    for side_name, data in [("【我方出战】", p), ("【敌方出战】", o)]:
        sp = data.get("species") or {}
        sp_names = sp.get("names") or {}
        name = sp_names.get("zh-Hans") or sp.get("name") or "未知"
        sp_id = data.get("species_id", 0)
        level = data.get("level", "?")
        gender_code = data.get("gender")
        gender = "♂ 雄性" if gender_code == "male" else ("♀ 雌性" if gender_code == "female" else "无性别")
        ab = data.get("ability") or {}
        ab_name = ab.get("name", "无")
        cur_hp = data.get("current_hp", 0)
        max_hp = data.get("max_hp", 1)
        hp_pct = round(cur_hp / max_hp * 100, 1) if max_hp > 0 else 0

        stats = data.get("stats") or {}
        atk = stats.get("attack", "-")
        defe = stats.get("defense", "-")
        spa = stats.get("special_attack", "-")
        spd = stats.get("special_defense", "-")
        spe = stats.get("speed", "-")

        print(f"\n{side_name} {name} (No.{sp_id}) | {gender} | 等级: Lv.{level}")
        print(f"  HP: {cur_hp}/{max_hp} ({hp_pct}%) | 特性: {ab_name}")
        print(f"  能力值: 物攻={atk} | 物防={defe} | 特攻={spa} | 特防={spd} | 速度={spe}")
        print(f"  技能列表:")
        moves = data.get("moves") or []
        if moves:
            for m in moves:
                slot = m.get("slot")
                m_name = m.get("name")
                m_type = m.get("type", "一般")
                dmg_cls = m.get("damage_class", "物理")
                pwr = m.get("power") if m.get("power") is not None else "—"
                acc = m.get("accuracy") if m.get("accuracy") is not None else "—"
                pp = f"{m.get('current_pp')}/{m.get('max_pp')}"
                prio = m.get("priority", 0)
                prio_str = f" [先制:{prio:+d}]" if prio != 0 else ""
                effect = m.get("effect_summary") or "普通物理攻击，无追加效果。"
                print(f"    [槽位 {slot}] {m_name} ({m_type}/{dmg_cls}){prio_str} | 威力:{pwr} | 命中:{acc} | PP:{pp}")
                print(f"            └ 机制效果: {effect}")
        else:
            print("    (技能数据加载中)")

    print("\n" + "=" * 58 + "\n")

if __name__ == "__main__":
    main()
