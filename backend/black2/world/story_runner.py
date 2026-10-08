"""High-level autonomous story progression orchestrator for Pokémon Black 2.

Combines:
1. ProgressionStateService (badges, money, gates, completed milestones)
2. GoalMemoryManager (active milestone, clear conditions, directives)
3. WorldGraph (macro Unova zone pathfinder with StoryGate gating)
4. PlayerRuntime (live zone, coordinates, facing, movement state)
into a unified, deterministic story decision planner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..state.memory_goals import goal_memory_manager, STORY_MILESTONES
from ..world.world_graph import world_graph_service
from ..world.player_coordinates import canonical_grid_player
from ..world.runtime_player_state import player_runtime_service
from ..progression.state import progression_state_service

# Milestone target zone mappings
MILESTONE_TARGET_ZONES: dict[str, int] = {
    "M1_RANCH_HERDIER": 446,      # Floccesy Ranch
    "M2_CHEREN_BASIC_BADGE": 489,  # Aspertia Gym (Cheren)
    "M3_ROXIE_TOXIC_BADGE": 502,   # Virbank Gym (Roxie)
    "M4_BURGH_INSECT_BADGE": 488,  # Castelia Gym (Burgh)
    "M5_ELESA_BOLT_BADGE": 63,     # Nimbasa Gym (Elesa)
    "M6_CLAY_QUAKE_BADGE": 97,     # Driftveil Gym (Clay)
    "M7_SKYLA_JET_BADGE": 108,     # Mistralton Gym (Skyla)
    "M8_DRAYDEN_FREEZE_BADGE": 121,# Opelucid Gym (Drayden)
    "M9_PLASMA_CLIMAX": 473,       # Humilau Gym / Giant Chasm
    "M10_LEAGUE_CHAMPION": 570,    # Pokémon League Champion Chamber
}


@dataclass
class StoryProgressionPlan:
    status: str
    active_milestone_id: str
    milestone_title: str
    milestone_description: str
    action_directive: str
    current_zone: int
    current_zone_name_zh: str
    current_zone_name_en: str
    target_zone: int
    target_zone_name_zh: str
    target_zone_name_en: str
    badges_count: int
    badge_mask: int
    world_route_traversable: bool
    world_route_status: str
    total_macro_steps: int
    next_macro_step: Optional[dict[str, Any]]
    blocked_by_gate: Optional[dict[str, Any]]
    milestones_completed: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "black2-story-progression-plan/v1",
            "status": self.status,
            "milestone": {
                "id": self.active_milestone_id,
                "title": self.milestone_title,
                "description": self.milestone_description,
                "directive": self.action_directive,
                "completed": self.milestones_completed,
            },
            "location": {
                "current_zone": self.current_zone,
                "current_zone_name_zh": self.current_zone_name_zh,
                "current_zone_name_en": self.current_zone_name_en,
                "target_zone": self.target_zone,
                "target_zone_name_zh": self.target_zone_name_zh,
                "target_zone_name_en": self.target_zone_name_en,
            },
            "progression": {
                "badges_count": self.badges_count,
                "badge_mask": self.badge_mask,
                "badge_mask_hex": f"0x{self.badge_mask:02X}",
            },
            "world_route": {
                "traversable": self.world_route_traversable,
                "status": self.world_route_status,
                "total_steps": self.total_macro_steps,
                "next_step": self.next_macro_step,
                "blocked_by_gate": self.blocked_by_gate,
            },
        }


class StoryRunner:
    """Computes next high-level story action given live progression and world topology."""

    def __init__(self) -> None:
        pass

    async def plan_next_story_action(self) -> dict[str, Any]:
        # 1. Read live player state
        player = canonical_grid_player(player_runtime_service.latest, require_resolved=False)
        current_zone = player.get("zone_id") if isinstance(player, dict) else None
        if not isinstance(current_zone, int):
            return {
                "format": "black2-story-progression-plan/v1",
                "status": "unresolved",
                "reason": "Player current Zone is not resolved in PlayerRuntime.",
            }

        # 2. Read live progression state (badges, money)
        prog_sample = await progression_state_service.sample()
        badge_count = int(prog_sample.get("badges", {}).get("count", 0) or 0)
        badge_mask = int(prog_sample.get("badges", {}).get("mask", 0) or 0)

        # 3. Evaluate active milestone
        goal = goal_memory_manager.evaluate({
            "party_count": 6,
            "badges": badge_count,
            "zone_id": current_zone,
        })
        active_ms_id = goal.active_milestone_id
        target_zone = MILESTONE_TARGET_ZONES.get(active_ms_id, 121)

        # 4. Find macro route
        route = world_graph_service.find_route(
            current_zone,
            target_zone,
            badge_mask=badge_mask,
            badge_count=badge_count,
        )

        curr_zh, curr_en = world_graph_service.get_zone_name(current_zone)
        targ_zh, targ_en = world_graph_service.get_zone_name(target_zone)

        next_step = route["steps"][0] if route.get("steps") else None

        # Find milestone metadata
        ms_meta = next((m for m in STORY_MILESTONES if m["id"] == active_ms_id), {})

        plan = StoryProgressionPlan(
            status="ready" if route.get("traversable") else "blocked_by_story_gate",
            active_milestone_id=active_ms_id,
            milestone_title=ms_meta.get("title", active_ms_id),
            milestone_description=ms_meta.get("description", goal.immediate),
            action_directive=goal.action_directive,
            current_zone=current_zone,
            current_zone_name_zh=curr_zh,
            current_zone_name_en=curr_en,
            target_zone=target_zone,
            target_zone_name_zh=targ_zh,
            target_zone_name_en=targ_en,
            badges_count=badge_count,
            badge_mask=badge_mask,
            world_route_traversable=route.get("traversable", False),
            world_route_status=route.get("status", "unknown"),
            total_macro_steps=route.get("step_count", 0),
            next_macro_step=next_step,
            blocked_by_gate=route.get("blocked_by_gate"),
            milestones_completed=goal.milestones_completed,
        )
        return plan.as_dict()

    async def execute_story_step(self) -> dict[str, Any]:
        """Execute one autonomous step towards the active story milestone."""
        plan = await self.plan_next_story_action()
        if plan.get("status") == "unresolved":
            return {
                "format": "black2-story-step-execution/v1",
                "status": "unresolved",
                "executed": False,
                "reason": plan.get("reason", "Player state unresolved."),
            }

        if not plan.get("world_route", {}).get("traversable"):
            return {
                "format": "black2-story-step-execution/v1",
                "status": "blocked_by_story_gate",
                "executed": False,
                "blocked_by_gate": plan.get("world_route", {}).get("blocked_by_gate"),
                "reason": "Macro path is blocked by a StoryGate (missing badge or progression flag).",
            }

        cur_zone = plan["location"]["current_zone"]
        targ_zone = plan["location"]["target_zone"]

        if cur_zone == targ_zone:
            return {
                "format": "black2-story-step-execution/v1",
                "status": "at_target_zone",
                "executed": True,
                "milestone": plan["milestone"],
                "directive": plan["milestone"]["directive"],
                "message": f"Already at target zone {cur_zone} ({plan['location']['target_zone_name_zh']}). Ready for milestone objective.",
            }

        next_step = plan.get("world_route", {}).get("next_step")
        if not next_step:
            return {
                "format": "black2-story-step-execution/v1",
                "status": "no_next_step",
                "executed": False,
                "reason": "Route has no next step available.",
            }

        step_kind = next_step.get("kind")
        step_to = next_step.get("to_zone")
        to_name = next_step.get("to_name_zh")
        action_desc = f"Proceed via {step_kind} from Zone {cur_zone} ({next_step.get('from_name_zh')}) to Zone {step_to} ({to_name})"

        return {
            "format": "black2-story-step-execution/v1",
            "status": "ready_for_execution",
            "executed": True,
            "step": next_step,
            "action_description": action_desc,
            "step_kind": step_kind,
            "current_zone": cur_zone,
            "destination_zone": step_to,
            "milestone_id": plan["milestone"]["id"],
        }



story_runner = StoryRunner()
