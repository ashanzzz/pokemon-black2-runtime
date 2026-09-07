from pathlib import Path
from tempfile import TemporaryDirectory

from backend.black2.world.navigation_planning import NavigationPlanService, NavigationPlanningError
from backend.black2.world.observed_navigation import ObservedNavigationGraph


def player(frame, x, z):
    return {
        "status": "resolved", "confidence": "verified", "frame": frame, "zone_id": 439,
        "position": {"grid": {"x": x, "y": 1, "z": z}, "world": {"x": x * 16 + 8, "y": 0, "z": z * 16 + 8}},
        "locomotion": {"transport_mode": "OnFoot"},
    }


def graph_sample(frame, x, z):
    return {"frame": frame, "zone_id": 439, "grid": {"x": x, "y": 1, "z": z}, "world": {"x": x * 16 + 8, "y": 0, "z": z * 16 + 8}}


def test_observed_route_cannot_escape_allowed_region_tile_set():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.observe_player(graph_sample(1, 10, 10))
        graph.observe_player(graph_sample(2, 11, 10))
        graph.observe_player(graph_sample(3, 12, 10))
        latest = player(4, 10, 10)
        planner = NavigationPlanService(graph, lambda: latest)
        destination = {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 439, "x": 12, "y": 1, "z": 10}
        allowed = [
            {"zone_id": 439, "x": 10, "y": 1, "z": 10},
            {"zone_id": 439, "x": 12, "y": 1, "z": 10},
        ]
        try:
            planner.create_plan(destination, allowed_nodes=allowed)
        except NavigationPlanningError as exc:
            assert exc.code == "NAV_NO_ROUTE"
            assert "allowed navigation subgraph" in str((exc.details or {}).get("reason"))
        else:
            raise AssertionError("expected route outside allowed Tile Set to be rejected")
