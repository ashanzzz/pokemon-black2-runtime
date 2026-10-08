"""Pokémon Black 2 - Main Story Milestones & Ultimate Clear Goal System.

Defines:
1. Exact Game Clear Condition (殿堂入赏 / Hall of Fame / Champion Iris defeated)
2. 10 Main Story Progression Milestones (M1 ~ M10)
3. Three-Tier Goal Memory (Immediate, Mid-Term, Long-Term, Action Directive)
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


ULTIMATE_CLEAR_CONDITION = (
    "挑战宝可梦联盟冠军之路，战胜四天王（婉龙、越橘、嘉德丽雅、连武）与合众新冠军艾莉丝（Iris），"
    "完成殿堂入赏登记（Hall of Fame），触发制作人员名单（THE END）并自动完成通关存档。"
)

# 10 主线剧情阶段与通关里程碑定义
STORY_MILESTONES = [
    {
        "id": "M1_RANCH_HERDIER",
        "title": "算木牧场事件与启程",
        "badge_req": 0,
        "description": "前往算木牧场寻找迷路的宝可梦，战胜劲敌修并接受阿戴克指导",
        "target_zone": 446,
        "completed_when": "获得城镇地图与招式学习器迁怒，返回桧扇市",
    },
    {
        "id": "M2_CHEREN_BASIC_BADGE",
        "title": "第 1 徽章：基础徽章 (Basic Badge)",
        "badge_req": 1,
        "description": "挑战桧扇道馆馆主黑连（一般属性），战胜后获得基础徽章与TM83自我激励",
        "target_zone": 427,
        "completed_when": "badges >= 1",
    },
    {
        "id": "M3_ROXIE_TOXIC_BADGE",
        "title": "第 2 徽章：毒性徽章 (Toxic Badge)",
        "badge_req": 2,
        "description": "前往立涌市工业区，挑战立涌道馆馆主霍米加（毒属性），乘船前往飞云市",
        "target_zone": 451,
        "completed_when": "badges >= 2",
    },
    {
        "id": "M4_BURGH_INSECT_BADGE",
        "title": "第 3 徽章：甲虫徽章 (Insect Badge)",
        "badge_req": 3,
        "description": "清剿飞云下水道等离子队，挑战飞云道馆馆主亚堤（虫属性）",
        "target_zone": 381,
        "completed_when": "badges >= 3",
    },
    {
        "id": "M5_ELESA_BOLT_BADGE",
        "title": "第 4 徽章：伏特徽章 (Bolt Badge)",
        "badge_req": 4,
        "description": "穿越4号道路与荒野名胜区，挑战雷文道馆馆主小菊儿（电属性）",
        "target_zone": 470,
        "completed_when": "badges >= 4",
    },
    {
        "id": "M6_CLAY_QUAKE_BADGE",
        "title": "第 5 徽章：震动徽章 (Quake Badge)",
        "badge_req": 5,
        "description": "穿过帆巴大桥，挑战帆巴道馆馆主菊老大（地面属性），参加PWT锦标赛",
        "target_zone": 490,
        "completed_when": "badges >= 5",
    },
    {
        "id": "M7_SKYLA_JET_BADGE",
        "title": "第 6 徽章：飞翼徽章 (Jet Badge)",
        "badge_req": 6,
        "description": "穿过电石山洞，挑战吹寄道馆馆主风露（飞行属性），搭乘飞机前往山路镇",
        "target_zone": 510,
        "completed_when": "badges >= 6",
    },
    {
        "id": "M8_DRAYDEN_FREEZE_BADGE",
        "title": "第 7 徽章：冰冻徽章 (Freeze Badge)",
        "badge_req": 7,
        "description": "穿过反转山脉与笼目镇，到达双龙市挑战夏卡（龙属性）",
        "target_zone": 530,
        "completed_when": "badges >= 7",
    },
    {
        "id": "M9_PLASMA_CLIMAX",
        "title": "第 8 徽章与等离子队决战",
        "badge_req": 8,
        "description": "青海波市击败西子伊（海浪徽章），攻破等离子驱逐舰，巨人洞窟战胜暗黑酋雷姆与魁奇思",
        "target_zone": 550,
        "completed_when": "badges >= 8 且 等离子队事件平息",
    },
    {
        "id": "M10_LEAGUE_CHAMPION",
        "title": "宝可梦联盟与殿堂入赏（通关终点）",
        "badge_req": 8,
        "description": "穿越23号公路与冠军之路，战胜四天王（婉龙、越橘、嘉德丽雅、连武）与冠军艾莉丝，登入殿堂！",
        "target_zone": 570,
        "completed_when": "击败冠军艾莉丝，完成殿堂入赏登记，保存通关存档",
    },
]


class GoalTier(BaseModel):
    immediate: str
    mid_term: str
    long_term: str
    action_directive: str
    priority_target: Optional[Dict[str, Any]] = None
    milestones_completed: List[str] = Field(default_factory=list)
    clear_condition: str = ULTIMATE_CLEAR_CONDITION
    active_milestone_id: str = "M1_RANCH_HERDIER"


class GoalMemoryManager:
    """Evaluates live RAM state (badges, party, map, coordinates, flags) to determine goals."""

    def __init__(self):
        self.completed_milestones: List[str] = []

    def evaluate(self, state_dict: Dict[str, Any]) -> GoalTier:
        party_count = state_dict.get("party_count", 0)
        badges = state_dict.get("badges", 0) or 0
        zone_id = state_dict.get("zone_id")

        if party_count == 0:
            immediate = "向北穿过桧扇市街道，前往西北方展望台寻找白露（Bianca）领取初始宝可梦"
            mid_term = "在展望台获得初学者宝可梦与宝可梦图鉴，完成劲敌初战"
            long_term = ULTIMATE_CLEAR_CONDITION
            directive = "【最高优先级】: 前往桧扇市展望台寻找白露"
            target = {"name": "桧扇市展望台 白露"}
            active_id = "M0_OPENING"
        elif badges == 0:
            # Check if player is already in Floccesy Ranch (Zone 446)
            if zone_id == 446:
                immediate = "在算木牧场深入探索，寻找迷路的小约克并战胜劲敌修"
                mid_term = "解决牧场事件后返回桧扇市，挑战桧扇道馆馆主黑连获得基础徽章"
                long_term = ULTIMATE_CLEAR_CONDITION
                directive = "【最高优先级】: 深入算木牧场触发寻回小约克剧情并战胜修"
                target = {"zone_id": 446, "name": "算木牧场深处"}
                active_id = "M1_RANCH_HERDIER"
            else:
                immediate = "前往桧扇市训练家学校/道馆，向黑连发起第1道馆挑战"
                mid_term = "战胜黑连获得第1枚徽章（基础徽章），通过19号道路关卡进入合众大地图"
                long_term = ULTIMATE_CLEAR_CONDITION
                directive = "【最高优先级】: 前往桧扇道馆挑战黑连"
                target = {"name": "桧扇道馆"}
                active_id = "M2_CHEREN_BASIC_BADGE"
        elif badges == 1:
            immediate = "穿过20号公路前往立涌市，挑战立涌道馆霍米加"
            mid_term = "获得毒性徽章后乘船前往合众核心大都市——飞云市"
            long_term = ULTIMATE_CLEAR_CONDITION
            directive = "【最高优先级】: 前往立涌市挑战霍米加"
            target = {"name": "立涌道馆"}
            active_id = "M3_ROXIE_TOXIC_BADGE"
        elif badges == 2:
            immediate = "探索飞云市下水道击退等离子队，挑战飞云道馆亚堤"
            mid_term = "获得甲虫徽章，北上4号公路前往雷文市"
            long_term = ULTIMATE_CLEAR_CONDITION
            directive = "【最高优先级】: 解决飞云下水道事件并挑战亚堤"
            target = {"name": "飞云道馆"}
            active_id = "M4_BURGH_INSECT_BADGE"
        elif badges == 3:
            immediate = "穿过4号道路荒野名胜区，挑战雷文道馆小菊儿"
            mid_term = "获得伏特徽章，跨过帆巴大桥进军帆巴市"
            long_term = ULTIMATE_CLEAR_CONDITION
            directive = "【最高优先级】: 挑战雷文道馆小菊儿"
            target = {"name": "雷文道馆"}
            active_id = "M5_ELESA_BOLT_BADGE"
        elif badges == 4:
            immediate = "前往帆巴道馆挑战菊老大，随后参加PWT锦标赛"
            mid_term = "获得震动徽章，穿越电石山洞前往吹寄市"
            long_term = ULTIMATE_CLEAR_CONDITION
            directive = "【最高优先级】: 挑战帆巴道馆菊老大"
            target = {"name": "帆巴道馆"}
            active_id = "M6_CLAY_QUAKE_BADGE"
        elif badges == 5:
            immediate = "穿过电石山洞，挑战吹寄道馆风露"
            mid_term = "获得飞翼徽章，搭乘吹寄机场飞机前往反转山脉与山路镇"
            long_term = ULTIMATE_CLEAR_CONDITION
            directive = "【最高优先级】: 挑战吹寄道馆风露"
            target = {"name": "吹寄道馆"}
            active_id = "M7_SKYLA_JET_BADGE"
        elif badges == 6:
            immediate = "穿过反转山脉与笼目镇，挑战双龙道馆夏卡"
            mid_term = "获得冰冻徽章，直面被等离子巡航舰冰封的合众大危机"
            long_term = ULTIMATE_CLEAR_CONDITION
            directive = "【最高优先级】: 挑战双龙道馆夏卡"
            target = {"name": "双龙道馆"}
            active_id = "M8_DRAYDEN_FREEZE_BADGE"
        elif badges == 7:
            immediate = "前往青海波市挑战西子伊获得海浪徽章，攻破等离子驱逐舰"
            mid_term = "在巨人洞窟决战暗黑酋雷姆与魁奇思，拯救合众地区"
            long_term = ULTIMATE_CLEAR_CONDITION
            directive = "【最高优先级】: 获得第8徽章并击溃等离子队魁奇思"
            target = {"name": "青海波道馆 / 巨人洞窟"}
            active_id = "M9_PLASMA_CLIMAX"
        else:
            immediate = "穿越23号公路与险峻的冠军之路，向宝可梦联盟大殿进军"
            mid_term = "连续击败四天王（婉龙、越橘、嘉德丽雅、连武）并直面冠军艾莉丝"
            long_term = ULTIMATE_CLEAR_CONDITION
            directive = "【最高优先级】: 决战宝可梦联盟，战胜艾莉丝登入殿堂达成通关！"
            target = {"name": "宝可梦联盟 殿堂入赏室"}
            active_id = "M10_LEAGUE_CHAMPION"

        completed = list(self.completed_milestones)
        if badges >= 1 or party_count > 0:
            if "M1_RANCH_HERDIER" not in completed:
                completed.append("M1_RANCH_HERDIER")
        if badges >= 1 and "M2_CHEREN_BASIC_BADGE" not in completed:
            completed.append("M2_CHEREN_BASIC_BADGE")
        if badges >= 2 and "M3_ROXIE_TOXIC_BADGE" not in completed:
            completed.append("M3_ROXIE_TOXIC_BADGE")
        if badges >= 3 and "M4_BURGH_INSECT_BADGE" not in completed:
            completed.append("M4_BURGH_INSECT_BADGE")
        if badges >= 4 and "M5_ELESA_BOLT_BADGE" not in completed:
            completed.append("M5_ELESA_BOLT_BADGE")
        if badges >= 5 and "M6_CLAY_QUAKE_BADGE" not in completed:
            completed.append("M6_CLAY_QUAKE_BADGE")
        if badges >= 6 and "M7_SKYLA_JET_BADGE" not in completed:
            completed.append("M7_SKYLA_JET_BADGE")
        if badges >= 7 and "M8_DRAYDEN_FREEZE_BADGE" not in completed:
            completed.append("M8_DRAYDEN_FREEZE_BADGE")
        if badges >= 8 and "M9_PLASMA_CLIMAX" not in completed:
            completed.append("M9_PLASMA_CLIMAX")

        return GoalTier(
            immediate=immediate,
            mid_term=mid_term,
            long_term=long_term,
            action_directive=directive,
            priority_target=target,
            milestones_completed=completed,
            clear_condition=ULTIMATE_CLEAR_CONDITION,
            active_milestone_id=active_id,
        )


goal_memory_manager = GoalMemoryManager()
