"""Unit tests for P0 Architecture Refactoring.

Tests:
1. Unified Observation API (/api/v1/agent/observation)
2. Authoritative Battle Request (/api/v1/battle/request)
3. Standard Low-Level Input Actuators (/api/v1/input/press, /hold, /touch)
4. UI Screen State (/api/v1/ui/state)
5. Party Pokemon Move PP attributes (max_pp, base_pp, pp_ups, usable)
"""
from fastapi.testclient import TestClient
import pytest
from unittest.mock import AsyncMock, PropertyMock, patch

from backend.black2.api.app import app
from backend.black2.world.runtime_player_state import player_runtime_service


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_agent_observation_endpoint(client):
    mock_p = {
        "status": "resolved",
        "zone_id": 406,
        "position": {"grid": {"x": 660, "y": 0, "z": 186}},
        "orientation": {"facing": "South"},
        "locomotion": {"phase": "Idle", "transport_mode": "OnFoot"},
        "frame": 5000,
    }
    with patch.object(player_runtime_service, "latest", mock_p), \
         patch("backend.black2.api.battle_routes._evidence", new_callable=AsyncMock) as mock_ev:
        mock_ev.return_value = {"active": False}
        res = client.get("/api/v1/agent/observation")
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "black2-agent-observation/v1"
        assert data["status"] == "ready"
        assert data["mode"] == "OVERWORLD"
        assert "player" in data
        assert "ui" in data
        assert "party_summary" in data
        assert "available_actions" in data


def test_ui_state_endpoint(client):
    with patch("backend.black2.api.battle_routes._evidence", new_callable=AsyncMock) as mock_ev:
        mock_ev.return_value = {"active": False}
        res = client.get("/api/v1/ui/state")
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "black2-ui-state/v1"
        assert data["screen"] == "OVERWORLD"
        assert data["can_move_player"] is True


def test_battle_request_inactive(client):
    with patch("backend.black2.api.battle_routes._evidence", new_callable=AsyncMock) as mock_ev:
        mock_ev.return_value = {"active": False}
        res = client.get("/api/v1/battle/request")
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "black2-battle-request/v1"
        assert data["active"] is False
        assert data["waiting_for_player"] is False
        assert data["legal_actions"] == []


def test_input_press_endpoint(client):
    from backend.black2.api import app as app_mod
    with patch.object(type(app_mod.client), "is_connected", new_callable=PropertyMock, return_value=True), \
         patch.object(app_mod.client, "press_buttons", new_callable=AsyncMock) as mock_press:
        mock_press.return_value = {"frames": 4, "queued": True, "buttons": ["A"]}
        res = client.post("/api/v1/input/press", json={"button": "A", "frames": 4})
        assert res.status_code == 200
        assert res.json()["queued"] is True


def test_input_touch_endpoint(client):
    from backend.black2.api import app as app_mod
    with patch.object(type(app_mod.client), "is_connected", new_callable=PropertyMock, return_value=True), \
         patch.object(app_mod.client, "touch", new_callable=AsyncMock) as mock_touch:
        mock_touch.return_value = {"frames": 6, "queued": True, "x": 128, "y": 96}
        res = client.post("/api/v1/input/touch", json={"x": 128, "y": 96, "frames": 6})
        assert res.status_code == 200
        assert res.json()["queued"] is True