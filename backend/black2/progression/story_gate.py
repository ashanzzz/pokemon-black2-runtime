"""Story gate definitions and real-time progression evaluator for Pokémon Black 2.

Evaluates whether regional gates, route barriers, gym locks, and transport
connectors are open based on verified badges and EventWork state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class StoryGateDefinition:
    gate_id: str
    name_zh: str
    name_en: str
    source_zone: int
    destination_zone: int
    required_badge_count: int
    required_badges: tuple[int, ...]
    blocking_reason_zh: str
    blocking_reason_en: str
    key_item_required: Optional[int] = None
    key_item_name_en: Optional[str] = None


UNOVA_STORY_GATES: tuple[StoryGateDefinition, ...] = (
    StoryGateDefinition(
        gate_id="gate_route19_aspertia",
        name_zh="19号道路桧扇市大门",
        name_en="Route 19 Aspertia Gate",
        source_zone=427,
        destination_zone=441,
        required_badge_count=0,
        required_badges=(),
        blocking_reason_zh="需要完成初始宝可梦领取与对战",
        blocking_reason_en="Requires receiving starter Pokémon and first rival battle",
    ),
    StoryGateDefinition(
        gate_id="gate_route20_virbank",
        name_zh="20号道路立涌市大门",
        name_en="Route 20 Virbank Gate",
        source_zone=446,
        destination_zone=447,
        required_badge_count=1,
        required_badges=(1,),
        blocking_reason_zh="需要战胜桧扇道馆黑连，获得基础徽章",
        blocking_reason_en="Requires Basic Badge from Gym Leader Cheren in Aspertia City",
    ),
    StoryGateDefinition(
        gate_id="gate_virbank_ferry",
        name_zh="立涌市码头轮渡（前往飞云市）",
        name_en="Virbank Port Ferry to Castelia City",
        source_zone=448,
        destination_zone=378,
        required_badge_count=2,
        required_badges=(1, 2),
        blocking_reason_zh="需要战胜立涌道馆霍米加获得毒性徽章，并击退联合厂区等离子队",
        blocking_reason_en="Requires Toxic Badge from Roxie and clearing Team Plasma at Virbank Complex",
    ),
    StoryGateDefinition(
        gate_id="gate_castelia_route4",
        name_zh="飞云市4号道路北侧关卡",
        name_en="Castelia City Route 4 Gate",
        source_zone=378,
        destination_zone=461,
        required_badge_count=3,
        required_badges=(1, 2, 3),
        blocking_reason_zh="需要平息飞云下水道事件，并战胜飞云道馆亚堤获得甲虫徽章",
        blocking_reason_en="Requires Insect Badge from Burgh and clearing Castelia Sewers",
    ),
    StoryGateDefinition(
        gate_id="gate_driftveil_drawbridge",
        name_zh="帆巴吊桥（5号道路 -> 帆巴市）",
        name_en="Driftveil Drawbridge from Route 5",
        source_zone=464,
        destination_zone=482,
        required_badge_count=4,
        required_badges=(1, 2, 3, 4),
        blocking_reason_zh="需要战胜雷文道馆小菊儿获得伏特徽章后放下吊桥",
        blocking_reason_en="Requires Bolt Badge from Elesa to lower the drawbridge",
    ),
    StoryGateDefinition(
        gate_id="gate_chargestone_cave",
        name_zh="电石山洞入口（6号道路 -> 电石山洞）",
        name_en="Chargestone Cave Entrance",
        source_zone=485,
        destination_zone=511,
        required_badge_count=5,
        required_badges=(1, 2, 3, 4, 5),
        blocking_reason_zh="需要战胜帆巴道馆菊老大获得震动徽章，并参加PWT后由菊老大移除电网",
        blocking_reason_en="Requires Quake Badge from Clay and PWT completion to clear the cave gate",
    ),
    StoryGateDefinition(
        gate_id="gate_mistralton_plane",
        name_zh="吹寄机场飞机（吹寄市 -> 山路镇/反转山脉）",
        name_en="Mistralton Cargo Plane to Lentimas Town",
        source_zone=107,
        destination_zone=518,
        required_badge_count=6,
        required_badges=(1, 2, 3, 4, 5, 6),
        blocking_reason_zh="需要战胜吹寄道馆风露获得飞翼徽章后搭乘飞机",
        blocking_reason_en="Requires Jet Badge from Skyla to fly to Lentimas Town",
    ),
    StoryGateDefinition(
        gate_id="gate_reversal_mountain_opelucid",
        name_zh="反转山脉与11号道路关卡（通往双龙市）",
        name_en="Route 11 to Opelucid City Gate",
        source_zone=525,
        destination_zone=120,
        required_badge_count=6,
        required_badges=(1, 2, 3, 4, 5, 6),
        blocking_reason_zh="需要穿过反转山脉、小波镇与笼目镇，前往双龙市",
        blocking_reason_en="Requires traversing Reversal Mountain and Undella/Lacunosa to reach Opelucid City",
    ),
    StoryGateDefinition(
        gate_id="gate_marine_tube_humilau",
        name_zh="海底隧道（双龙市 -> 青海波市）",
        name_en="Marine Tube to Humilau City",
        source_zone=120,
        destination_zone=472,
        required_badge_count=7,
        required_badges=(1, 2, 3, 4, 5, 6, 7),
        blocking_reason_zh="需要战胜双龙道馆夏卡获得冰冻徽章，并经历双龙市冰封事件后开放海底隧道",
        blocking_reason_en="Requires Freeze Badge from Drayden and resolving Opelucid freeze event",
    ),
    StoryGateDefinition(
        gate_id="gate_seaside_cave_humilau",
        name_zh="21号道路与海边洞穴道闸（通往青海波市）",
        name_en="Route 21 & Seaside Cave to Humilau City",
        source_zone=465,
        destination_zone=472,
        required_badge_count=7,
        required_badges=(1, 2, 3, 4, 5, 6, 7),
        blocking_reason_zh="需要战胜双龙道馆夏卡获得冰冻徽章，并在海边洞穴使用阿克罗马机器驱赶岩殿居蟹后方可通行",
        blocking_reason_en="Requires Freeze Badge from Drayden and Colress Machine at Seaside Cave",
    ),
    StoryGateDefinition(
        gate_id="gate_victory_road_badges",
        name_zh="23号公路与冠军之路8枚徽章检验门",
        name_en="Route 23 & Victory Road 8-Badge Gates",
        source_zone=565,
        destination_zone=566,
        required_badge_count=8,
        required_badges=(1, 2, 3, 4, 5, 6, 7, 8),
        blocking_reason_zh="需要集齐合众地区全部8枚道馆徽章方可通过8重徽章道闸",
        blocking_reason_en="Requires all 8 Unova Gym Badges to pass the Badge Check Gates",
    ),
    StoryGateDefinition(
        gate_id="gate_pokemon_league_champion",
        name_zh="宝可梦联盟冠军大殿大门",
        name_en="Pokémon League Champion Chamber",
        source_zone=566,
        destination_zone=570,
        required_badge_count=8,
        required_badges=(1, 2, 3, 4, 5, 6, 7, 8),
        blocking_reason_zh="需要击败全部四天王（婉龙、越橘、嘉德丽雅、连武）后开启中央冠军升降机",
        blocking_reason_en="Requires defeating all Elite Four members to unlock the Champion chamber",
    ),
)


def evaluate_story_gates(
    badge_mask: int,
    badge_count: int,
    *,
    key_item_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate all known Unova story gates against verified player badges."""
    key_items = key_item_ids or set()
    evaluated = []

    for defn in UNOVA_STORY_GATES:
        badges_ok = True
        missing_badges = []
        for badge_id in defn.required_badges:
            idx = badge_id - 1
            if not bool(badge_mask & (1 << idx)):
                badges_ok = False
                missing_badges.append(badge_id)

        count_ok = badge_count >= defn.required_badge_count
        item_ok = defn.key_item_required is None or defn.key_item_required in key_items

        unlocked = bool(badges_ok and count_ok and item_ok)
        status = "unlocked" if unlocked else "active"

        gate_record = {
            "gate_id": defn.gate_id,
            "name_zh": defn.name_zh,
            "name_en": defn.name_en,
            "source_zone": defn.source_zone,
            "destination_zone": defn.destination_zone,
            "status": status,
            "unlocked": unlocked,
            "blocking": not unlocked,
            "requirements": {
                "required_badge_count": defn.required_badge_count,
                "required_badges": list(defn.required_badges),
                "key_item_required": defn.key_item_required,
                "missing_badges": missing_badges,
            },
            "reason_zh": defn.blocking_reason_zh if not unlocked else "前置条件已达成，通道已开启",
            "reason_en": defn.blocking_reason_en if not unlocked else "Requirements met; passage is open",
        }
        evaluated.append(gate_record)

    return evaluated
