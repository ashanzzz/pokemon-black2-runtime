"""Unit tests for atomic battle APIs and closed-loop execution."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from unittest.mock import AsyncMock, patch

from backend.black2.api import battle_routes as routes
from backend.black2.api.battle_routes import router


@pytest.fixture(autouse=True)
def reset_battle_route_runtime(monkeypatch):
    for name in ("_reader", "_hub", "_action_engine", "_trainer_catalog", "_trainer_catalog_error"):
        monkeypatch.setattr(routes, name, None)
    for decoder_name in ("_decoder", "_ui_cursor_decoder", "_identity_decoder", "_party_decoder", "_inventory_decoder"):
        decoder = getattr(routes, decoder_name)
        if hasattr(decoder, "configure"):
            decoder.configure(None)


@pytest.fixture
def api() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_post_battle_move_schema_and_execution(api):
    with patch("backend.black2.battle.battle_action_service.battle_action_service.execute_decision", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {
            "format": "black2-battle-action-execution/v1",
            "status": "executed",
            "executed": True,
            "verification": {"code": "MOVE_EXECUTED_VERIFIED", "move_slot": 2, "pp_decreased": True},
        }
        res = api.post("/api/v1/battle/move", json={"move_slot": 2})
        assert res.status_code == 200
        body = res.json()
        assert body["executed"] is True
        assert body["verification"]["move_slot"] == 2
        mock_exec.assert_called_once_with({"type": "use_move", "move_slot": 2, "target": "opponent:0"}, request_id=None)


def test_post_battle_switch_schema_and_execution(api):
    with patch("backend.black2.battle.battle_action_service.battle_action_service.execute_decision", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {
            "format": "black2-battle-action-execution/v1",
            "status": "executed",
            "executed": True,
            "verification": {"code": "SWITCH_EXECUTED_VERIFIED", "party_slot": 3},
        }
        res = api.post("/api/v1/battle/switch", json={"party_slot": 3})
        assert res.status_code == 200
        body = res.json()
        assert body["executed"] is True
        assert body["verification"]["party_slot"] == 3


def test_post_battle_item_schema_and_execution(api):
    with patch("backend.black2.battle.battle_action_service.battle_action_service.execute_decision", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {
            "format": "black2-battle-action-execution/v1",
            "status": "executed",
            "executed": True,
            "verification": {"code": "ITEM_USED_VERIFIED"},
        }
        res = api.post("/api/v1/battle/item", json={"item_id": 17, "target_party_slot": 1})
        assert res.status_code == 200
        body = res.json()
        assert body["executed"] is True


def test_post_battle_catch_schema_and_execution(api):
    with patch("backend.black2.battle.battle_action_service.battle_action_service.execute_decision", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {
            "format": "black2-battle-action-execution/v1",
            "status": "executed",
            "executed": True,
            "verification": {"code": "POKEBALL_THROWN_SUCCESS", "caught": True},
        }
        res = api.post("/api/v1/battle/catch", json={"item_id": 4})
        assert res.status_code == 200
        body = res.json()
        assert body["executed"] is True


def test_post_battle_flee_when_already_out_of_battle(api):
    with patch.object(routes, "_evidence", new_callable=AsyncMock) as mock_ev:
        mock_ev.return_value = {"active": False}
        res = api.post("/api/v1/battle/flee")
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["status"] == "not_in_battle"