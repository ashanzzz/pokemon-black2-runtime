"""Pokémon Black 2 - Three-Tier Goal Memory & AI Directive System.

Manages:
1. Immediate Goal (近期即时目标 / 最高优先级任务)
2. Mid-Term Goal (中期里程碑目标)
3. Long-Term Goal (远期终极主线目标)
4. AI Action Directive (当前动态记忆行动提示词)
"""

from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class GoalTier(BaseModel):
    immediate: str
    mid_term: str
    long_term: str
    action_directive: str
    priority_target: Optional[Dict[str, Any]] = None
    milestones_completed: List[str] = Field(default_factory=list)


class GoalMemoryManager:
    """Evaluates live RAM state (badges, party, map, coordinates, flags) to determine goals."""

    def __init__(self):
        self.completed_milestones: List[str] = []

    def evaluate(self, state_dict: Dict[str, Any]) -> GoalTier:
        party_count = state_dict.get("party_count", 0)
        badges = state_dict.get("badges", 0)
        map_name = state_dict.get("location", "")
        coords = state_dict.get("player_world_pos", {})
        cx = coords.get("x")
        cy = coords.get("y")
        ctx = state_dict.get("context", {})
        st_type = ctx.get("screen_type", "")

        # Default Opening Story Goals (开局阶段)
        if party_count == 0:
            # Player does not have a starter Pokémon yet
            immediate = "向北穿过桧扇市街道，前往西北方展望台（Lookout Point）寻找白露（Bianca）"
            mid_term = "在展望台获得初学者宝可梦与宝可梦图鉴，战胜劲敌修的初战，获取城镇地图"
            long_term = "前往桧扇道馆挑战黑连获得基础徽章，经19号道路前往立涌市"
            directive = "【最高优先级】: 前往展望台 (X=16, Y=705) 寻找戴大帽子的白露博士助手"
            target = {"x": 16, "y": 705, "name": "展望台 白露所在处"}
        elif badges == 0:
            # Has Pokémon, before 1st gym
            immediate = "前往桧扇市训练家学校/道馆，向黑连发起道馆馆主挑战"
            mid_term = "战胜黑连获得基础徽章，通过19号道路关卡进入合众大地图"
            long_term = "挑战合众地区全部 8 个道馆并进军宝可梦联盟冠军之路"
            directive = "【最高优先级】: 前往桧扇道馆 (X=47, Y=704) 挑战黑连"
            target = {"x": 47, "y": 704, "name": "桧扇道馆"}
        else:
            # 1 or more badges
            immediate = "沿着19号道路向东前往算木镇与算木牧场"
            mid_term = "在算木牧场寻找迷路的扒手猫，接受阿戴克的指导"
            long_term = "前往立涌市挑战霍米加毒系道馆"
            directive = "【最高优先级】: 前往算木镇 (X=100, Y=704)"
            target = {"x": 100, "y": 704, "name": "算木镇入口"}

        return GoalTier(
            immediate=immediate,
            mid_term=mid_term,
            long_term=long_term,
            action_directive=directive,
            priority_target=target,
            milestones_completed=self.completed_milestones,
        )


goal_memory_manager = GoalMemoryManager()
