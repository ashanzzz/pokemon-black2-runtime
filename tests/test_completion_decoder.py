import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.decoders.completion_decoder import CompletionDecoder
from backend.black2.api.progression_routes import router as prog_router
from backend.black2.progression.state import progression_state_service
from backend.black2.world.runtime_player_state import player_runtime_service


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(prog_router)
    return TestClient(app)


@pytest.mark.anyio
async def test_completion_decoder_initial_in_progress(monkeypatch):
    # Mock player in Virbank (Zone 448) with 6 badges
    monkeypatch.setattr(
        player_runtime_service,
        "latest",
        {"status": "resolved", "zone_id": 448, "position": {"grid": {"x": 200, "y": 0, "z": 650}}},
    )
    async def mock_prog():
        return {"status": "verified", "badges": {"status": "verified", "count": 6, "mask": 0x3F}}
    monkeypatch.setattr(progression_state_service, "sample", mock_prog)

    decoder = CompletionDecoder()
    sample = await decoder.sample()

    assert sample["format"] == "black2-completion-state/v1"
    assert sample["status"] == "in_progress"
    assert sample["stage"] == "collecting_gym_badges"
    assert sample["completion"]["game_cleared"] is False
    assert sample["completion"]["champion_defeated"] is False
    assert sample["progression_summary"]["badges_count"] == 6
    assert sample["progression_summary"]["all_badges_obtained"] is False


@pytest.mark.anyio
async def test_completion_decoder_league_champion_chamber(monkeypatch):
    # Mock player in Champion Chamber (Zone 570) with 8 badges
    monkeypatch.setattr(
        player_runtime_service,
        "latest",
        {"status": "resolved", "zone_id": 570, "position": {"grid": {"x": 10, "y": 0, "z": 20}}},
    )
    async def mock_prog():
        return {"status": "verified", "badges": {"status": "verified", "count": 8, "mask": 0xFF}}
    monkeypatch.setattr(progression_state_service, "sample", mock_prog)

    decoder = CompletionDecoder()
    sample = await decoder.sample()

    assert sample["status"] == "in_progress"
    assert sample["stage"] == "champion_chamber"
    assert sample["league_context"]["in_league_facility"] is True
    assert "Iris" in sample["league_context"]["facility_name"]


def test_completion_api_endpoint(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        player_runtime_service,
        "latest",
        {"status": "resolved", "zone_id": 448, "position": {"grid": {"x": 200, "y": 0, "z": 650}}},
    )
    async def mock_prog():
        return {"status": "verified", "badges": {"status": "verified", "count": 6, "mask": 0x3F}}
    monkeypatch.setattr(progression_state_service, "sample", mock_prog)

    res = client.get("/api/v1/progression/completion")
    assert res.status_code == 200
    data = res.json()
    assert data["format"] == "black2-completion-state/v1"
    assert data["status"] == "in_progress"
