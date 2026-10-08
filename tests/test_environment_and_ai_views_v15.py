from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.api import current_view_routes, status_routes
from backend.black2.api import navigation_routes
from backend.black2.world.navigation_context import NavigationContextCompiler


def _snapshot() -> dict:
    return {
        "sampled_at": 10.0,
        "age_seconds": 0.2,
        "runtime": {"status": "ready", "semantic_status": "ready"},
        "transport": {"bridge_connected": True, "session_id": "s1", "frame": 99},
        "player": {
            "status": "resolved", "frame": 99, "zone_id": 446, "matrix_id": 0,
            "position": {"grid": {"x": 141, "y": 2, "z": 659}},
            "locomotion": {"transport_mode": "OnFoot", "phase": "Moving", "gait": "Walking", "gait_confidence": "calibrated"},
            "environment": {"tile_under": {"class": 4, "flags": 0}},
            "props": {"status": "probable", "source": "FieldPropSystem", "source_frame": 99, "day_part": 2, "previous_day_part": 1, "day_part_changed": 1, "season": 3},
        },
        "battle": {"active": None},
    }


def test_environment_payload_keeps_runtime_and_static_weather_separate(monkeypatch):
    header = SimpleNamespace(matrix_id=0, weather=4, battle_bg=7, enable_running=True, enable_cycling=False, enable_fly_from=True)
    fake_provider = SimpleNamespace(rom=SimpleNamespace(zone=lambda _zone: header))
    monkeypatch.setattr(navigation_routes, "navigation_static_provider", lambda: fake_provider)
    body = status_routes._environment_payload(_snapshot())
    assert body["read_only"] is True
    assert body["writes_performed"] is False
    assert body["transport"]["mode"]["value"] == "OnFoot"
    assert body["tile"]["material"]["kind"] == "tall_grass"
    assert body["overworld"]["time_of_day"]["value"] == 2
    assert body["overworld"]["season"]["value"] == 3
    assert body["overworld"]["zone_weather"]["value"] == 4
    assert body["battle"]["weather"]["status"] == "unresolved"
    assert body["zone_static"]["battle_background"]["value"] == 7


def test_local_7x7_view_is_explicitly_not_claimed_as_nds_camera(monkeypatch):
    class Projection:
        def tile(self, zone_id, x, y, z, *, include_raw=False):
            return {
                "status": "decoded", "coordinate": {"zone_id": zone_id, "x": x, "y": y, "z": z},
                "rom": {"matrix_id": 0, "owner_zone_id": zone_id},
                "surfaces": [{"layer_index": 0, "material": {"kind": "ground"}, "collision": {"static_blocked": False}, "height": {}}],
            }
    class Hub:
        def snapshot(self):
            return _snapshot()
    monkeypatch.setattr(current_view_routes, "_hub", Hub())
    monkeypatch.setattr(current_view_routes, "_static_world", Projection())
    app = FastAPI()
    app.include_router(current_view_routes.router)
    body = TestClient(app).get("/api/v1/ai/view/current?profile=local_7x7").json()
    assert body["mode"] == "local_static_window_candidate"
    assert len(body["tiles"]) == 49
    assert body["coverage"]["bounds"] == {"width": 7, "height": 7, "max_tiles": 49}
    assert body["view_policy"]["is_current_nds_view"] is False


def test_navigation_context_emits_candidate_trainer_sight_without_identity_promotion():
    context = NavigationContextCompiler().compile(
        player={"zone_id": 446, "frame": 4},
        static_entities={"npcs": [{"id": 8, "record_index": 8, "x": 10, "y": 20, "z": 2, "direction_raw": 3, "sight_raw": 3}]},
    )
    sight = next(item for item in context["constraints"] if item["kind"] == "trainer_sight")
    assert len(sight["tiles"]) == 3
    assert sight["status"] == "candidate"
    assert sight["metadata"]["identity_status"] == "trainer_unverified"
    assert sight["tiles"][0] == {"zone_id": 446, "x": 11, "y": 2, "z": 20}
