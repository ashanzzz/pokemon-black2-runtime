from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.api import navigation_routes
from backend.black2.world.navigation_planning import NavigationPlanService
from backend.black2.world.navigation_tasks import NavigationTaskService
from backend.black2.world.observed_navigation import ObservedNavigationGraph


def raw_player(frame: int, x: int, y: int, z: int, zone: int = 427):
    return {
        "status": "resolved",
        "confidence": "verified",
        "frame": frame,
        "zone_id": zone,
        "position": {
            "grid": {"x": x, "y": y, "z": z},
            "world": {"x": x * 16 + 8, "y": y * 16, "z": z * 16 + 8},
        },
        "locomotion": {"phase": "Idle", "semantic_state": "Standing"},
    }


def graph_player(frame: int, x: int, y: int, z: int, zone: int = 427):
    return {
        "frame": frame,
        "zone_id": zone,
        "grid": {"x": x, "y": y, "z": z},
        "world": {"x": x * 16 + 8, "y": y * 16, "z": z * 16 + 8},
    }


def controllable_snapshot():
    return {
        "runtime": {"status": "ready"},
        "semantic": {
            "map_loaded": True,
            "ready_for_input": True,
            "context": {
                "screen_type": "OVERWORLD",
                "can_move_player": True,
                "is_dialogue_active": False,
            },
        },
    }


def client_for(graph: ObservedNavigationGraph, latest):
    navigation_routes.configure_navigation_routes(
        NavigationPlanService(graph, lambda: latest)
    )
    app = FastAPI()
    app.include_router(navigation_routes.router)
    return TestClient(app)


def restore_public_planner():
    navigation_routes.configure_navigation_routes(
        NavigationPlanService(
            navigation_routes.observed_navigation_graph,
            lambda: navigation_routes.player_runtime_service.latest,
        )
    )


def test_plan_returns_drawable_layered_path():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.observe_player(graph_player(1, 10, 2, 10))
        graph.observe_player(graph_player(2, 11, 2, 10))
        graph.observe_player(graph_player(3, 12, 3, 10))
        client = client_for(graph, raw_player(4, 10, 2, 10))

        response = client.post(
            "/api/v1/navigation/plans",
            json={
                "destination": {
                    "type": "grid",
                    "space": "gen5-field-grid-v1",
                    "zone_id": 427,
                    "x": 12,
                    "y": 3,
                    "z": 10,
                }
            },
        )

        assert response.status_code == 200
        plan = response.json()
        assert plan["status"] == "ready"
        assert plan["cost"]["steps"] == 2
        assert plan["segments"][0]["path"] == [
            {"zone_id": 427, "x": 10, "y": 2, "z": 10},
            {"zone_id": 427, "x": 11, "y": 2, "z": 10},
            {"zone_id": 427, "x": 12, "y": 3, "z": 10},
        ]
    restore_public_planner()


def test_plan_accepts_explicit_grid_start_without_player_runtime():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.observe_player(graph_player(1, 10, 2, 10))
        graph.observe_player(graph_player(2, 11, 2, 10))
        client = client_for(graph, None)

        response = client.post(
            "/api/v1/navigation/plans",
            json={
                "start": {
                    "type": "grid",
                    "space": "gen5-field-grid-v1",
                    "zone_id": 427,
                    "x": 10,
                    "y": 2,
                    "z": 10,
                },
                "destination": {
                    "type": "grid",
                    "space": "gen5-field-grid-v1",
                    "zone_id": 427,
                    "x": 11,
                    "y": 2,
                    "z": 10,
                },
            },
        )

        assert response.status_code == 200
        plan = response.json()
        assert plan["resolved_start"] == {
            "zone_id": 427,
            "position": {"x": 10, "y": 2, "z": 10},
            "frame": None,
            "source": "explicit_grid",
            "confidence": "candidate",
        }
        assert plan["cost"]["steps"] == 1
    restore_public_planner()


def test_invalid_explicit_start_has_specific_error_contract():
    with TemporaryDirectory() as td:
        client = client_for(ObservedNavigationGraph(project_root=Path(td)), None)
        destination = {
            "type": "grid",
            "space": "gen5-field-grid-v1",
            "zone_id": 427,
            "x": 11,
            "y": 2,
            "z": 10,
        }
        invalid_starts = [
            {
                "type": "grid",
                "space": "unsupported-grid",
                "zone_id": 427,
                "x": 10,
                "y": 2,
                "z": 10,
            },
            {
                "type": "grid",
                "space": "gen5-field-grid-v1",
                "zone_id": 427,
                "x": 10,
                "z": 10,
            },
        ]

        for start in invalid_starts:
            response = client.post(
                "/api/v1/navigation/plans",
                headers={"X-Request-ID": "invalid-start-test"},
                json={"start": start, "destination": destination},
            )
            assert response.status_code == 422
            error = response.json()["error"]
            assert error["code"] == "NAV_INVALID_START"
            assert error["trace_id"] == "invalid-start-test"
            assert error["details"]["validation_errors"]
    restore_public_planner()


def test_execution_request_rejects_explicit_start():
    with TemporaryDirectory() as td:
        client = client_for(ObservedNavigationGraph(project_root=Path(td)), None)
        coordinate = {
            "type": "grid",
            "space": "gen5-field-grid-v1",
            "zone_id": 427,
            "x": 10,
            "y": 2,
            "z": 10,
        }
        response = client.post(
            "/api/v1/navigation/tasks",
            json={"start": coordinate, "destination": coordinate},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "NAV_INVALID_REQUEST"
    restore_public_planner()


def test_request_schema_forbids_unknown_fields():
    with TemporaryDirectory() as td:
        client = client_for(ObservedNavigationGraph(project_root=Path(td)), None)
        response = client.post(
            "/api/v1/navigation/plans",
            headers={"X-Request-ID": "api-test-1"},
            json={
                "destination": {
                    "type": "grid",
                    "space": "gen5-field-grid-v1",
                    "zone_id": 427,
                    "x": 1,
                    "y": 0,
                    "z": 2,
                    "goal_y": 99,
                }
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "NAV_INVALID_REQUEST"
        assert response.json()["error"]["trace_id"] == "api-test-1"
    restore_public_planner()


def test_unresolved_player_and_cross_zone_have_stable_errors():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        unresolved = client_for(graph, None).post(
            "/api/v1/navigation/plans",
            json={"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 1, "y": 0, "z": 2}},
        )
        assert unresolved.status_code == 409
        assert unresolved.json()["error"]["code"] == "NAV_PLAYER_UNRESOLVED"

        cross_zone = client_for(graph, raw_player(1, 1, 0, 2, zone=427)).post(
            "/api/v1/navigation/plans",
            json={"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 428, "x": 1, "y": 0, "z": 2}},
        )
        assert cross_zone.status_code == 409
        assert cross_zone.json()["error"]["code"] == "NAV_TRANSITION_UNVERIFIED"
    restore_public_planner()


def test_capabilities_explicitly_disable_execution():
    with TemporaryDirectory() as td:
        response = client_for(ObservedNavigationGraph(project_root=Path(td)), None).get(
            "/api/v1/navigation/capabilities"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["planning"]["read_only"] is True
        assert body["planning"]["observations_endpoint"].startswith("/api/v1/navigation/observations")
        assert body["execution"]["available"] is False
    restore_public_planner()


def test_observations_endpoint_returns_bounded_static_preview_component():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.observe_player(graph_player(1, 10, 2, 10))
        graph.observe_player(graph_player(2, 11, 2, 10))
        client = client_for(graph, None)

        response = client.get(
            "/api/v1/navigation/observations?zone_id=427&x=9&y=2&z=10&limit=10"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["format"] == "black2-navigation-observations/v1"
        assert body["start"]["x"] == 10
        assert body["reachable_node_count"] == 2
        assert body["execution_eligible"] is False

        invalid = client.get("/api/v1/navigation/observations?zone_id=427&x=9")
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "NAV_INVALID_REQUEST"
    restore_public_planner()


def test_safe_planner_rejects_inferred_reverse_edges():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.observe_player(graph_player(1, 10, 0, 10))
        graph.observe_player(graph_player(2, 11, 0, 10))
        client = client_for(graph, raw_player(3, 11, 0, 10))
        response = client.post(
            "/api/v1/navigation/plans",
            json={"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 10, "y": 0, "z": 10}},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "NAV_NO_ROUTE"
    restore_public_planner()


def test_direct_walk_promotes_previously_inferred_reverse_edge():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.observe_player(graph_player(1, 10, 0, 10))
        graph.observe_player(graph_player(2, 11, 0, 10))
        graph.observe_player(graph_player(3, 10, 0, 10))
        planner = NavigationPlanService(graph, lambda: raw_player(4, 11, 0, 10))
        plan = planner.create_plan(
            {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 10, "y": 0, "z": 10}
        )
        assert plan["cost"]["steps"] == 1


class FakeBridge:
    is_connected = True

    def __init__(self, latest):
        self.latest = latest
        self.clear_count = 0

    async def press_buttons(self, buttons, frames=4):
        assert buttons == ["Right"]
        self.latest["position"]["grid"]["x"] += 1
        self.latest["position"]["world"]["x"] += 16
        self.latest["frame"] += frames
        return {"queued": True}

    async def clear_inputs(self):
        self.clear_count += 1
        return {"ok": True}


def test_task_succeeds_only_after_canonical_arrival():
    import asyncio

    async def scenario():
        with TemporaryDirectory() as td:
            graph = ObservedNavigationGraph(project_root=Path(td))
            graph.observe_player(graph_player(1, 10, 0, 10))
            graph.observe_player(graph_player(2, 11, 0, 10))
            latest = raw_player(3, 10, 0, 10)
            bridge = FakeBridge(latest)
            planner = NavigationPlanService(graph, lambda: latest)
            tasks = NavigationTaskService(
                planner, bridge, lambda: latest,
                control_sample=controllable_snapshot, poll_seconds=0.001,
            )
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                max_steps=1,
            )
            await tasks._runners[started["task_id"]]
            finished = tasks.get(started["task_id"])
            assert finished["status"] == "succeeded", finished["stop_reason"]
            assert finished["arrival"]["position"] == {"x": 11, "y": 0, "z": 10}
            assert bridge.clear_count == 1

    asyncio.run(scenario())
