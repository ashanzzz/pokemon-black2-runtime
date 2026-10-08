"""High-level multi-zone autonomous global navigation pipeline.

Orchestrates sequential micro NavigationTasks across the WorldGraph macro
route, handling inter-zone warps, continuous matrix seams, battle interrupts,
and automatic fleeing to deliver true end-to-end autonomous travel.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .location_catalog import resolve_zone_poi
from .navigation_tasks import NavigationTaskService, NavigationPlanningError
from .world_graph import world_graph_service
from .runtime_player_state import player_runtime_service

logger = logging.getLogger("black2.global_navigation")

# Pre-verified physical border waypoints across Matrix 0 seams
SEAM_WAYPOINTS: dict[tuple[int, int], dict[str, Any]] = {
    (456, 448): {"x": 210, "y": 0, "z": 672},
    (446, 439): {"x": 128, "y": 2, "z": 662},
    (439, 437): {"x": 96, "y": 1, "z": 694},
}



class GlobalNavigationTaskService:
    """Pipelines macro-level multi-zone routes into sequential verified micro tasks."""

    def __init__(self, task_service: NavigationTaskService) -> None:
        self.task_service = task_service
        self._active_global_tasks: dict[str, dict[str, Any]] = {}

    def get_global_task(self, task_id: str) -> dict[str, Any] | None:
        return self._active_global_tasks.get(task_id)

    async def start_global_task(
        self,
        goal_zone: int,
        *,
        poi: str | None = None,
        destination: dict[str, Any] | None = None,
        movement_mode: str = "auto",
        flee_wild_battles: bool = True,
    ) -> dict[str, Any]:
        """Start a multi-zone navigation pipeline towards goal_zone."""
        goal_zone = int(goal_zone)
        player = player_runtime_service.latest or {}
        start_zone = int(player.get("zone_id") or 457)

        # 1. Resolve final destination coordinate
        final_dest = destination
        poi_desc = None
        if not final_dest and poi:
            resolved_poi = resolve_zone_poi(goal_zone, poi)
            if resolved_poi:
                final_dest = {
                    "type": "grid",
                    "space": "gen5-field-grid-v1",
                    "zone_id": goal_zone,
                    "x": resolved_poi["x"],
                    "y": resolved_poi.get("y", 0),
                    "z": resolved_poi["z"],
                }
                poi_desc = resolved_poi.get("description")
        
        if not final_dest:
            # Fallback to Pokemon Center in goal zone
            center = resolve_zone_poi(goal_zone, "pokemon_center")
            if center:
                final_dest = {
                    "type": "grid",
                    "space": "gen5-field-grid-v1",
                    "zone_id": goal_zone,
                    "x": center["x"],
                    "y": center.get("y", 0),
                    "z": center["z"],
                }
                poi_desc = center.get("description")
            else:
                raise NavigationPlanningError(
                    "NAV_GLOBAL_DEST_UNRESOLVED",
                    f"Could not resolve destination coordinate or POI {poi!r} in Zone {goal_zone}.",
                    status_code=400,
                )

        # 2. Compute macro route via WorldGraph
        from ..progression.state import progression_state_service
        prog = progression_state_service.latest or {}
        badges_info = prog.get('badges') or {}
        badge_mask = int(badges_info.get('mask', 0x3F) or 0x3F)
        badge_count = int(badges_info.get('count', 6) or 6)
        macro_route = world_graph_service.find_route(start_zone, goal_zone, badge_mask=badge_mask, badge_count=badge_count)
        if not macro_route.get("traversable"):
            raise NavigationPlanningError(
                "NAV_GLOBAL_UNREACHABLE",
                f"No traversable WorldGraph route from Zone {start_zone} to Zone {goal_zone}.",
                status_code=409,
                details=macro_route,
            )

        task_id = f"gnav_{uuid4().hex}"
        record = {
            "format": "black2-global-navigation-task/v1",
            "task_id": task_id,
            "status": "queued",
            "start_zone": start_zone,
            "goal_zone": goal_zone,
            "poi": poi,
            "poi_description": poi_desc,
            "final_destination": final_dest,
            "movement_mode": movement_mode,
            "flee_wild_battles": flee_wild_battles,
            "macro_route": macro_route,
            "zone_path": macro_route.get("zone_path") or [start_zone, goal_zone],
            "current_step_index": 0,
            "total_steps": len(macro_route.get("steps") or []),
            "current_micro_task_id": None,
            "created_at": time.time(),
            "updated_at": time.time(),
            "completed_steps": [],
            "error": None,
        }
        self._active_global_tasks[task_id] = record

        # Run pipeline in background task
        asyncio.create_task(self._run_global_pipeline(record))
        return record

    async def _flee_battle_if_needed(self) -> bool:
        """Helper to touch flee and wait for return to OVERWORLD."""
        try:
            from ..api.battle_routes import _evidence, _battle_identity, _action_engine, _wait_for_battle_move_settle
            ev = await _evidence()
            if not ev.get("active"):
                return True
            ident = await _battle_identity(ev)
            if (ident.get("battle_kind") or {}).get("value") == "trainer":
                return False  # Cannot flee trainer
            if _action_engine is not None:
                await _action_engine.touch_screen(128, 178, hold_frames=8)
                after, _, _ = await _wait_for_battle_move_settle(timeout_sec=6.0, auto_advance=True)
                for _ in range(5):
                    if not after.get("active"):
                        return True
                    await _action_engine.press_button("B", hold_frames=6, wait_frames=12)
                    await asyncio.sleep(0.2)
                    after = await _evidence()
                return not after.get("active")
        except Exception as exc:
            logger.warning("Auto-flee battle failed: %s", exc)
        return False

    async def _run_global_pipeline(self, record: dict[str, Any]) -> None:
        """Execute the multi-zone sequence."""
        record["status"] = "executing"
        goal_zone = record["goal_zone"]
        macro_steps = record["macro_route"].get("steps") or []

        try:
            step_idx = 0
            while step_idx <= len(macro_steps):
                record["updated_at"] = time.time()
                
                # Wait for player runtime to settle after potential map fade transitions
                player = None
                for _ in range(40):
                    p = player_runtime_service.latest or {}
                    if p.get("status") in {"resolved", "candidate"} and isinstance(p.get("zone_id"), int):
                        player = p
                        break
                    await asyncio.sleep(0.2)
                
                if player is None:
                    raise NavigationPlanningError("NAV_PLAYER_UNRESOLVED", "PlayerRuntime did not resolve within timeout.")

                cur_zone = int(player.get("zone_id"))

                # Synchronize macro step index with actual player zone
                matching_idx = next((i for i, s in enumerate(macro_steps) if s.get("from_zone") == cur_zone), None)
                if matching_idx is not None and matching_idx > step_idx:
                    logger.info("Player advanced to Zone %d; syncing macro step to %d", cur_zone, matching_idx)
                    step_idx = matching_idx

                record["current_step_index"] = step_idx

                if cur_zone == goal_zone:
                    # Final leg inside goal_zone
                    record["current_step_index"] = len(macro_steps)
                    final_dest = record["final_destination"]
                    micro = self.task_service.start(
                        destination=final_dest,
                        movement_mode=record["movement_mode"],
                    )
                    record["current_micro_task_id"] = micro.get("task_id")
                    res = await self._await_micro_task(record, micro.get("task_id"))
                    if res.get("status") in {"completed", "succeeded"}:
                        record["status"] = "succeeded"
                        record["arrival"] = res.get("arrival") or res.get("current")
                        return
                    else:
                        raise NavigationPlanningError("NAV_FINAL_STEP_FAILED", "Failed reaching final destination in goal zone.", details=res)

                if step_idx >= len(macro_steps):
                    # We ran out of macro steps but haven't reached goal zone
                    break

                step = macro_steps[step_idx]
                target_zone = step.get("to_zone")
                kind = step.get("kind")
                conn = step.get("connector_metadata") or {}

                # Determine waypoint in current zone
                if kind == "warp":
                    src_cand = conn.get("source_tile_candidate") or {}
                    wx = int(src_cand.get("x", 0))
                    wz = int(src_cand.get("z", 0))
                    dest_payload = {
                        "type": "grid", "space": "gen5-field-grid-v1",
                        "zone_id": cur_zone, "x": wx, "y": 0, "z": wz,
                    }
                else:
                    # matrix_seam: Look up known border seam waypoint to enter target_zone
                    seam_wp = SEAM_WAYPOINTS.get((cur_zone, target_zone))
                    if seam_wp:
                        dest_payload = {
                            "type": "grid", "space": "gen5-field-grid-v1",
                            "zone_id": cur_zone, "x": seam_wp["x"], "y": seam_wp.get("y", 0), "z": seam_wp["z"],
                        }
                    else:
                        dest_payload = copy.deepcopy(record["final_destination"])

                # Dispatch micro task
                micro = self.task_service.start(
                    destination=dest_payload,
                    movement_mode=record["movement_mode"],
                )
                record["current_micro_task_id"] = micro.get("task_id")
                res = await self._await_micro_task(record, micro.get("task_id"))

                if res.get("status") in {"completed", "succeeded"}:
                    record["completed_steps"].append({
                        "step_index": step_idx,
                        "from_zone": cur_zone,
                        "to_zone": target_zone,
                        "task_id": micro.get("task_id"),
                    })
                    step_idx += 1
                    if kind == "warp":
                        await asyncio.sleep(1.5)
                        for _ in range(10):
                            await asyncio.sleep(0.2)
                            p = player_runtime_service.latest or {}
                            if int(p.get("zone_id") or 0) == int(target_zone):
                                break
                else:
                    completed_micro_steps = int((res.get("progress") or {}).get("completed_steps") or 0)
                    stop_code = (res.get("stop_reason") or {}).get("code")
                    replan_recoverable = (
                        completed_micro_steps > 0
                        or stop_code in {
                            "NAV_PARTIAL_SEGMENT", "NAV_NOT_CONTROLLABLE", "NAV_LANDING_UNSETTLED",
                            "NAV_GRAPH_REVISION_CHANGED", "NAV_POSITION_DIVERGED"
                        }
                    )
                    if replan_recoverable:
                        logger.info("Micro task made partial progress (%d steps) or recoverable stop (%s); replanning...", completed_micro_steps, stop_code)
                        await asyncio.sleep(0.3)
                        continue
                    else:
                        raise NavigationPlanningError("NAV_MICRO_STEP_FAILED", f"Micro task failed on step {step_idx} ({kind} -> Zone {target_zone}).", details=res)

            record["status"] = "succeeded"
        except Exception as exc:
            logger.exception("Global navigation pipeline encountered error: %s", exc)
            record["status"] = "failed"
            record["error"] = str(exc)

    async def _await_micro_task(self, record: dict[str, Any], task_id: str, timeout_seconds: float = 120.0) -> dict[str, Any]:
        """Poll micro task and auto-flee wild battles if enabled."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.4)
            tdata = self.task_service.get(task_id) or {}
            st = tdata.get("status")

            if st in {"completed", "succeeded"}:
                return tdata

            if st in {"failed", "cancelled"}:
                stop_reason = tdata.get("stop_reason") or {}
                if "battle" in str(stop_reason).lower() and record.get("flee_wild_battles"):
                    # Auto-flee and resume
                    fled = await self._flee_battle_if_needed()
                    if fled:
                        await asyncio.sleep(0.5)
                        resumed = self.task_service.resume_task(task_id)
                        task_id = resumed.get("task_id") or task_id
                        continue
                return tdata
        return self.task_service.get(task_id) or {"status": "timeout"}
