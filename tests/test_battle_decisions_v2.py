from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.api.battle_routes import router


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_request_exposes_full_semantic_command_family():
    body = client().get("/api/v1/battle/request").json()
    assert set(body["decision_contracts"]) == {
        "use_move", "switch", "use_item", "throw_ball", "run", "shift", "rotate"
    }
    assert body["execution_available"] is False


def test_multi_actor_decision_validates_then_is_evidence_gated():
    body = {
        "battle_id": "example",
        "request_id": 17,
        "commands": [
            {"type": "use_move", "actor": "player:0", "move_slot": 1, "target": "opponent:0"},
            {"type": "switch", "actor": "player:1", "party_slot": 3},
        ],
    }
    response = client().post("/api/v1/battle/decisions", json=body)
    assert response.status_code == 409
    result = response.json()
    assert result["executed"] is False
    assert result["decision"] == body


def test_rotation_and_triple_position_commands_are_in_protocol():
    c = client()
    for command in (
        {"type": "shift", "actor": "player:1", "to_position": "center"},
        {"type": "rotate", "actor": "player:0", "direction": "right"},
        {"type": "throw_ball", "actor": "player:0", "item_id": 4, "target": "opponent:0"},
    ):
        r = c.post("/api/v1/battle/decisions", json={"commands": [command]})
        assert r.status_code == 409
        assert r.json()["decision"]["commands"][0] == command
