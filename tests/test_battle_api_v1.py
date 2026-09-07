"""Strict unresolved-state contract for battle APIs."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.black2.api.battle_routes import router
from backend.black2.observer.capabilities import CapabilityStatus, capability_store
from backend.black2.observer.presentation import build_observer_presentation


@pytest.fixture
def api() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_capabilities_disclose_research_status_and_execution_gate(api):
    body = api.get("/api/v1/battle/capabilities").json()
    assert body["format"] == "black2-battle-capabilities/v1"
    assert body["decoder"] == {
        "status": "RESEARCH",
        "verified": False,
        "confidence": 0.0,
        "can_detect": [],
    }
    assert body["execution"] == {
        "available": False,
        "requires_verified_current_state": True,
        "blind_menu_input_allowed": False,
    }
    assert set(body["action_contracts"]) == {"use_move", "switch", "use_item", "run"}
    assert body["evidence_requirements"]


def test_state_never_turns_missing_decoder_into_inactive_battle(api):
    body = api.get("/api/v1/battle/state").json()
    assert body["status"] == "unresolved"
    assert body["active"] is None
    assert body["active_status"] == "unresolved"
    assert body["battle_type"] == "unresolved"
    assert body["phase"] == "unresolved"
    assert body["menu"]["status"] == "unresolved"
    assert body["available_actions"] == []
    assert body["execution_available"] is False
    assert body["evidence"]["verified"] is False


def test_action_listing_has_no_executable_actions(api):
    body = api.get("/api/v1/battle/actions").json()
    assert body["status"] == "unresolved"
    assert body["execution_available"] is False
    assert body["available_actions"] == []
    assert all(contract["executable"] is False for contract in body["action_contracts"].values())
    assert body["reason"]["code"] == "BATTLE_RUNTIME_UNRESOLVED"


@pytest.mark.parametrize(
    "action",
    [
        {"type": "use_move", "slot": 1},
        {"type": "use_move", "slot": 4, "target": "opponent:0"},
        {"type": "switch", "party_slot": 6},
        {"type": "use_item", "item_id": 17},
        {"type": "use_item", "item_id": 17, "target": "party:1"},
        {"type": "run"},
    ],
)
def test_valid_actions_are_validated_then_rejected_without_ram_evidence(api, action):
    response = api.post(
        "/api/v1/battle/actions",
        json=action,
        headers={"x-request-id": "battle-test"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["executed"] is False
    assert body["action"] == action
    assert body["reason"]["code"] == "BATTLE_RUNTIME_UNRESOLVED"
    assert body["evidence_requirements"]
    assert body["request_id"] == "battle-test"


@pytest.mark.parametrize(
    "action",
    [
        {},
        {"type": "attack", "slot": 1},
        {"type": "use_move", "slot": 0},
        {"type": "use_move", "slot": 5},
        {"type": "use_move", "slot": "1"},
        {"type": "use_move", "slot": 1, "target": "opponent:3"},
        {"type": "switch", "party_slot": 0},
        {"type": "switch", "party_slot": 1, "slot": 1},
        {"type": "use_item", "item_id": 0},
        {"type": "use_item", "item_id": 1, "target": "party:0"},
        {"type": "run", "slot": 1},
    ],
)
def test_invalid_action_payloads_return_422(api, action):
    assert api.post("/api/v1/battle/actions", json=action).status_code == 422


def test_observer_capability_does_not_claim_battle_detection():
    capability = capability_store.capabilities["battle_system"]
    assert capability.status == CapabilityStatus.RESEARCH
    assert capability.confidence == 0.0
    assert capability.can_detect == []
    assert capability.validator == "none"


def test_observer_presentation_does_not_invent_battle_inactivity():
    presentation = build_observer_presentation({"frame": 1, "context": {}})
    detector = next(row for row in presentation.detectors if row["detector"] == "BattleSystem")
    assert detector == {
        "detector": "BattleSystem",
        "candidate": "UNRESOLVED",
        "confidence": 0.0,
        "evidence": "No verified battle runtime decoder",
    }
