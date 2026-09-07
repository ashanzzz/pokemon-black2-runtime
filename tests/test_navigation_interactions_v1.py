import asyncio
from pathlib import Path

from backend.black2.api import navigation_routes
from backend.black2.world.navigation_planning import NavigationPlanService
from backend.black2.world.navigation_tasks import NavigationTaskService
from backend.black2.world.observed_navigation import NavNode, ObservedNavigationGraph


def player(x: int, y: int, z: int, *, face_dir: int = 2) -> dict:
    return {
        "status": "resolved",
        "confidence": "verified",
        "frame": 1,
        "zone_id": 441,
        "position": {
            "grid": {"x": x, "y": y, "z": z},
            "world": {"x": x * 16 + 8, "y": y * 16, "z": z * 16 + 8},
        },
        "orientation": {"face_dir_raw": face_dir, "facing": {0: "North", 1: "South", 2: "West", 3: "East"}[face_dir]},
        "locomotion": {"phase": "Idle", "semantic_state": "Standing"},
    }


def controls():
    return {
        "runtime": {"status": "ready"},
        "semantic": {
            "map_loaded": True,
            "ready_for_input": True,
            "context": {"screen_type": "OVERWORLD", "can_move_player": True, "is_dialogue_active": False},
        },
    }


class RoomProvider:
    revision = "room-static"

    def status(self):
        return {"available": True, "revision": self.revision}

    def surface_at(self, zone_id, x, z, y, *, anchor=None):
        return {"walkable": 0 <= x <= 12 and 0 <= z <= 12, "cell": {"x": x, "y": y, "z": z}}

    def find_path(self, start, goal, *, player_sample=None, occupied=()):
        blocked = {
            (item["grid"]["x"], item["grid"]["z"])
            for item in navigation_routes.normalize_occupancy(occupied, default_zone=441, default_y=0)
        }
        if (goal.x, goal.z) in blocked:
            return {"reachable": False, "path": [], "reason": "occupied"}
        path = [start.public()] if start == goal else [start.public(), goal.public()]
        return {"reachable": True, "path": path, "cost": float(len(path) - 1)}

    def has_candidate_edge(self, start, goal, *, player_sample=None, occupied=()):
        return True


def test_npc_snap_chooses_stand_tile_and_facing():
    result = navigation_routes._snap_npc_interaction(
        RoomProvider(),
        zone_id=441,
        target=NavNode(441, 11, 0, 5),
        player_sample=player(11, 0, 7),
        occupancy=[{"zone_id": 441, "grid": {"x": 11, "y": 0, "z": 5}}],
        max_radius=12,
    )

    assert result["target"] == {"zone_id": 441, "x": 11, "y": 0, "z": 6}
    assert result["interaction"]["stand_tile"]["z"] == 6
    assert result["interaction"]["target"]["type"] == "grid"
    assert result["interaction"]["target"]["space"] == "gen5-field-grid-v1"
    assert result["interaction"]["facing"] == "North"
    assert result["interaction"]["turn_only"] is False


def test_direct_occupied_npc_goal_is_normalized_by_api_helper():
    destination = {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 441, "x": 11, "y": 0, "z": 5}
    resolved, interaction = navigation_routes._auto_interaction_goal(
        destination,
        provider=RoomProvider(),
        player_sample=player(11, 0, 7),
        occupancy=[{"zone_id": 441, "grid": {"x": 11, "y": 0, "z": 5}}],
        interaction=None,
    )

    assert resolved["x"] == 11 and resolved["z"] == 6
    assert interaction["target"]["z"] == 5
    assert interaction["facing"] == "North"


def test_interaction_task_turns_in_place_after_reaching_stand_tile(tmp_path: Path):
    async def scenario():
        graph = ObservedNavigationGraph(project_root=tmp_path)
        latest = player(11, 0, 6, face_dir=2)
        graph.observe_player(latest)

        class Bridge:
            is_connected = True

            def __init__(self):
                self.turns = []
                self.clear_count = 0

            async def press_buttons(self, buttons, frames=4):
                self.turns.append((buttons, frames))
                assert buttons in (["Up"], ["A"])
                latest["orientation"] = {"face_dir_raw": 0, "facing": "North"}
                latest["frame"] += frames
                return {"queued": True}

            async def clear_inputs(self):
                self.clear_count += 1
                return {"ok": True}

        bridge = Bridge()
        planner = NavigationPlanService(graph, lambda: latest, static_provider=RoomProvider())
        tasks = NavigationTaskService(
            planner, bridge, lambda: latest, control_sample=controls,
            poll_seconds=0.001, step_timeout_seconds=0.03,
        )
        interaction = {
            "kind": "npc",
            "target": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 441, "x": 11, "y": 0, "z": 5},
            "stand_tile": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 441, "x": 11, "y": 0, "z": 6},
            "facing": "North",
        }
        started = tasks.start(
            interaction["stand_tile"], max_steps=1, interaction=interaction, navigation_intent="interact",
        )
        await tasks._runners[started["task_id"]]
        finished = tasks.get(started["task_id"])
        assert finished["status"] == "succeeded", finished["stop_reason"]
        assert finished["arrival"]["interaction"]["facing"] == "North"
        assert bridge.turns == [(["Up"], 1), (["A"], 1)]
        assert bridge.clear_count == 1

    asyncio.run(scenario())


def test_dynamic_interaction_replans_for_moved_npc_and_replaces_stale_occupancy(tmp_path: Path):
    async def scenario():
        graph = ObservedNavigationGraph(project_root=tmp_path)
        latest = player(5, 0, 7)

        class Bridge:
            is_connected = True

        moved_actor = {
            "slot": 1,
            "actor_uid": 1,
            "zone_id": 441,
            "same_current_scene": True,
            "grid": {"x": 6, "y": 0, "z": 6},
        }
        planner = NavigationPlanService(graph, lambda: latest, static_provider=RoomProvider())
        tasks = NavigationTaskService(
            planner, Bridge(), lambda: latest, control_sample=controls,
            actor_sample=lambda: {"status": "resolved", "actors": [moved_actor]},
        )
        interaction = {
            "kind": "npc",
            "target": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 441, "x": 5, "y": 0, "z": 6},
            "stand_tile": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 441, "x": 5, "y": 0, "z": 7},
            "facing": "North",
            "actor_id": "1",
            "execute": True,
        }
        plan = planner.create_plan(
            interaction["stand_tile"], interaction=interaction, navigation_intent="interact",
            occupied=[{"zone_id": 441, "grid": {"x": 5, "y": 0, "z": 6}}],
        )
        record = {
            "task_id": "nav_dynamic_test",
            "plan_id": plan["plan_id"],
            "_dynamic_replan_count": 0,
            "_occupied": ({"zone_id": 441, "grid": {"x": 5, "y": 0, "z": 6}},),
        }

        refreshed, error = await tasks._maybe_replan_dynamic_interaction(record, plan)

        assert error is None
        assert refreshed is not None
        assert refreshed["interaction"]["target"]["x"] == 6
        assert refreshed["interaction"]["stand_tile"]["x"] == 6
        assert refreshed["interaction"]["stand_tile"]["z"] == 7
        assert record["_occupied"] == ({"zone_id": 441, "grid": {"x": 6, "y": 0, "z": 6}},)

    asyncio.run(scenario())
