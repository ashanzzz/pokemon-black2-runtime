from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.api.dialogue_routes import router, configure_dialogue_routes, _clean_dialogue_text

def test_clean_dialogue_text():
    raw = "Hello[SCROLL]\n\nWorld[CLEAR]\n\nTest[CLEAR]"
    cleaned = _clean_dialogue_text(raw)
    assert cleaned == "Hello\nWorld\nTest"

def test_dialogue_current_when_idle():
    mock_action = MagicMock()
    mock_state = MagicMock()
    mock_hub = MagicMock()
    
    mock_sample = MagicMock()
    mock_sample.model_dump.return_value = {
        "context": {
            "screen_type": "OVERWORLD",
            "is_dialogue_active": False,
            "can_move_player": True,
            "speaker": "无活跃对话",
            "dialogue_text": "",
        }
    }
    mock_state.sample_once = AsyncMock(return_value=mock_sample)
    
    configure_dialogue_routes(mock_action, mock_state, mock_hub)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    
    res = client.get("/api/v1/dialogue/current")
    assert res.status_code == 200
    data = res.json()
    assert data["format"] == "black2-dialogue-current/v1"
    assert data["is_active"] is False
    assert data["can_move_player"] is True

def test_dialogue_skip_when_already_overworld():
    mock_action = MagicMock()
    mock_state = MagicMock()
    mock_hub = MagicMock()
    
    mock_sample = MagicMock()
    mock_sample.model_dump.return_value = {
        "context": {
            "screen_type": "OVERWORLD",
            "is_dialogue_active": False,
            "can_move_player": True,
        }
    }
    mock_state.sample_once = AsyncMock(return_value=mock_sample)
    
    configure_dialogue_routes(mock_action, mock_state, mock_hub)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    
    res = client.post("/api/v1/dialogue/skip")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["completed"] is True
    assert data["stopped_reason"] == "already_overworld"
    assert data["steps_taken"] == 0
