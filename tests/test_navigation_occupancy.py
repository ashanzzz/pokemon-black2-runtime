import asyncio

from backend.black2.world.navigation_planning import (
    NavigationPlanService,
    normalize_occupancy,
)
from backend.black2.world.observed_navigation import NavNode, ObservedNavigationGraph


def player(x: int, y: int, z: int, zone: int = 441) -> dict:
    return {
        "status": "resolved",
        "confidence": "verified",
        "frame": 1,
        "zone_id": zone,
        "position": {
            "grid": {"x": x, "y": y, "z": z},
            "world": {"x": x * 16 + 8, "y": y * 16, "z": z * 16 + 8},
        },
    }


def test_normalize_occupancy_accepts_all_live_actor_coordinate_shapes():
    values = [
        {"zone_id": 441, "grid": {"x": 11, "y": 0, "z": 5}},
        {"zone_id": 441, "position": {"grid": {"x": 11, "y": 0, "z": 5}}},
        {"zone_id": 441, "x": 11, "y": 0, "z": 5},
        {"zone_id": 441, "world": {"x": 184, "y": 0, "z": 88}},
    ]

    assert normalize_occupancy(values, default_zone=441, default_y=0) == [
        {"zone_id": 441, "grid": {"x": 11, "y": 0, "z": 5}}
    ]


def test_planner_never_exposes_a_path_through_normalized_actor_occupancy(tmp_path):
    graph = ObservedNavigationGraph(project_root=tmp_path)
    graph.observe_player(player(0, 0, 0))
    graph.observe_player(player(1, 0, 0))
    graph.observe_player(player(2, 0, 0))

    class DetourProvider:
        revision = "test-static"

        def status(self):
            return {"available": True, "revision": self.revision}

        def find_path(self, start, goal, *, player_sample=None, occupied=()):
            assert normalize_occupancy(occupied, default_zone=441, default_y=0)
            return {
                "reachable": True,
                "path": [
                    start.public(),
                    NavNode(441, 0, 0, 1).public(),
                    NavNode(441, 1, 0, 1).public(),
                    NavNode(441, 2, 0, 1).public(),
                    goal.public(),
                ],
                "cost": 4.0,
                "world_revision": self.revision,
            }

        def has_candidate_edge(self, start, goal, *, player_sample=None, occupied=()):
            return True

    planner = NavigationPlanService(
        graph,
        lambda: player(0, 0, 0),
        static_provider=DetourProvider(),
    )
    plan = planner.create_plan(
        {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 441, "x": 2, "y": 0, "z": 0},
        occupied=[{"position": {"grid": {"x": 1, "y": 0, "z": 0}}}],
    )

    path = plan["segments"][0]["path"]
    assert all((point["x"], point["z"]) != (1, 0) for point in path[1:])


def test_runtime_actor_payload_canonicalizes_effective_zone_candidate():
    from backend.black2.api import navigation_routes

    payload = {
        "actors": [
            {
                "slot": 3,
                "zone_id": "0",
                "effective_zone_id_candidate": 441,
                "same_current_scene": True,
                "position": {"grid": {"x": 11, "y": 0, "z": 5}},
            },
            {"is_player": True, "zone_id": 441, "grid": {"x": 0, "y": 0, "z": 0}},
        ]
    }

    assert navigation_routes._actor_occupancy_payload(payload, zone_id=441, y=0) == [
        {"zone_id": 441, "grid": {"x": 11, "y": 0, "z": 5}}
    ]
