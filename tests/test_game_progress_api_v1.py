"""Stable progress, flag and cutscene contracts."""

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.black2.api import semantic_routes as routes


class Hub:
    def __init__(self):
        self.value = {
            "age_seconds": 0.1,
            "sampled_at": 123,
            "transport": {"bridge_connected": True, "session_id": "test"},
            "runtime": {"status": "ready", "semantic_status": "ready"},
            "semantic": {
                "frame": 77,
                "location": "Test town",
                "context": {"screen_type": "OVERWORLD", "can_move_player": True},
            },
            "profile": {"party_count": 1, "badges": 0},
            "player": {"status": "resolved", "position": {"grid": {"x": 1, "y": 0, "z": 2}}},
        }

    def snapshot(self):
        return deepcopy(self.value)


@pytest.fixture
def api(monkeypatch):
    hub = Hub()
    monkeypatch.setattr(routes, "_hub", hub)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), hub


def test_tasks_and_objectives_are_stable_and_candidate(api):
    client, _ = api
    tasks = client.get("/api/v1/game/tasks").json()
    objectives = client.get("/api/v1/game/objectives").json()
    assert tasks["format"] == "black2-game-tasks/v1"
    assert tasks["count"] == 1
    assert tasks["tasks"][0]["task_id"] == "story.immediate"
    assert tasks["tasks"][0]["confidence"] == "candidate"
    assert objectives["format"] == "black2-game-objectives/v1"
    assert objectives["objectives"]["immediate"]
    assert objectives["evidence"]["verified"] is False
    detail = client.get("/api/v1/game/tasks/story.immediate").json()
    assert detail["format"] == "black2-game-task/v1"
    assert detail["task"]["task_id"] == "story.immediate"
    assert client.get("/api/v1/game/tasks/does-not-exist").status_code == 404


def test_flags_do_not_turn_missing_decoder_into_false_values(api):
    client, hub = api
    body = client.get("/api/v1/game/flags").json()
    assert body["decode_status"] == "unresolved"
    assert body["flags"] == []
    assert body["evidence"]["verified"] is False
    hub.value["semantic"]["flags"] = {"story_001": True, "map_004": False}
    observed = client.get("/api/v1/game/flags").json()
    assert observed["count"] == 2
    assert {row["id"] for row in observed["flags"]} == {"story_001", "map_004"}


def test_cutscene_classifies_dialogue_and_unknown(api):
    client, hub = api
    idle = client.get("/api/v1/game/cutscene").json()
    assert idle["phase"] == "idle"
    assert idle["active"] is False
    hub.value["semantic"]["context"] = {
        "screen_type": "DIALOGUE_CHOICE",
        "is_dialogue_active": True,
        "can_move_player": False,
        "dialogue_text": "Choose",
        "choices": [{"index": 0, "label": "Yes"}],
    }
    dialogue = client.get("/api/v1/game/cutscene").json()
    assert dialogue["phase"] == "dialogue"
    assert dialogue["active"] is True
    assert dialogue["blocking"] is True
    assert dialogue["dialogue"]["text"] == "Choose"
    hub.value["semantic"]["context"] = {"screen_type": "RUNTIME_UNRESOLVED", "can_move_player": False}
    unknown = client.get("/api/v1/game/cutscene").json()
    assert unknown["phase"] == "unknown"
    assert unknown["active"] is None
    assert unknown["blocking"] is None
    assert unknown["dialogue"]["active"] is None
    assert unknown["evidence"]["verified"] is False


@pytest.mark.parametrize("failure_mode", ["bridge_disconnected", "runtime_unresolved", "stale_sample"])
def test_unknown_runtime_never_becomes_false_empty_idle_or_available(api, failure_mode):
    client, hub = api
    # Simulate retained last-good values.  They are useful historical evidence,
    # but cannot describe the current game after the bridge/sample is lost.
    hub.value["profile"] = {"party_count": 1, "badges": 0, "confidence": "verified"}
    hub.value["semantic"]["flags"] = {"story_001": False}
    hub.value["semantic"]["flags_decode_status"] = "verified"
    if failure_mode == "bridge_disconnected":
        hub.value["transport"]["bridge_connected"] = False
    elif failure_mode == "runtime_unresolved":
        hub.value["semantic"]["context"] = {
            "screen_type": "RUNTIME_UNRESOLVED",
            "can_move_player": False,
            "is_dialogue_active": False,
            "available_actions": [],
        }
    else:
        hub.value["age_seconds"] = 30

    state = client.get("/api/v1/game/state").json()
    assert state["status"] == "unresolved"
    assert state["can_act"] is None
    assert state["execution_available"] is False
    assert state["screen"]["can_move_player"] is None
    assert state["screen"]["is_dialogue_active"] is None
    assert state["screen"]["available_actions"] is None

    objectives = client.get("/api/v1/game/objectives").json()
    assert objectives["status"] == "unresolved"
    assert objectives["contents_known"] is False
    assert objectives["objectives"]["immediate"] is None
    assert objectives["objectives"]["milestones_completed"] is None

    tasks = client.get("/api/v1/game/tasks").json()
    assert tasks["status"] == "unresolved"
    assert tasks["contents_known"] is False
    assert tasks["count"] is None
    assert tasks["tasks"] == []

    flags = client.get("/api/v1/game/flags").json()
    assert flags["status"] == "unresolved"
    assert flags["contents_known"] is False
    assert flags["count"] is None
    assert flags["flags"] == []
    assert flags["last_observed_flags"][0]["value"] is False

    cutscene = client.get("/api/v1/game/cutscene").json()
    assert cutscene["status"] == "unresolved"
    assert cutscene["phase"] == "unknown"
    assert cutscene["active"] is None
    assert cutscene["blocking"] is None
    assert cutscene["can_move_player"] is None
    assert cutscene["dialogue"]["active"] is None
    assert cutscene["dialogue"]["choices"] is None

    party = client.get("/api/v1/game/party").json()
    assert party["count"] is None
    assert party["count_known"] is False
    assert party["contents_known"] is False

    inventory = client.get("/api/v1/game/inventory").json()
    assert inventory["contents_known"] is False
    assert inventory["item_count"] is None
    assert inventory["pocket_count"] is None
    assert inventory["evidence"]["confidence"] == "unresolved"
    assert inventory["evidence"]["runtime_observation"]["current"] is False


def test_missing_progress_does_not_fall_through_to_post_badge_objective(api):
    client, hub = api
    hub.value["profile"] = {"party_count": None, "badges": None, "confidence": "unresolved"}
    objectives = client.get("/api/v1/game/objectives").json()
    tasks = client.get("/api/v1/game/tasks").json()
    assert objectives["status"] == "unresolved"
    assert objectives["objectives"]["immediate"] is None
    assert tasks["status"] == "unresolved"
    assert tasks["count"] is None
