"""Strict unresolved-state contract for battle APIs."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.black2.api import battle_routes as routes
from backend.black2.api.battle_routes import router
from backend.black2.observer.capabilities import CapabilityStatus, capability_store
from backend.black2.observer.presentation import build_observer_presentation


@pytest.fixture(autouse=True)
def reset_battle_route_runtime(monkeypatch):
    """Keep router-only contract tests independent from the process app wiring."""
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


def test_capabilities_disclose_research_status_and_execution_gate(api):
    body = api.get("/api/v1/battle/capabilities").json()
    assert body["read_only"] is True
    assert body["writes_performed"] is False
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
    assert body["read_only"] is True
    assert body["mutation_policy"] == "cache_reads_only"
    assert body["status"] == "unresolved"
    assert body["active"] is None
    assert body["active_status"] == "unresolved"
    assert body["battle_type"] == "unresolved"
    assert body["phase"] == "unresolved"
    assert body["menu"]["status"] == "unresolved"
    assert body["available_actions"] == []
    assert body["execution_available"] is False
    assert body["evidence"]["verified"] is False


def test_evidence_route_policy_cannot_be_overridden_by_decoder_payload(api, monkeypatch):
    async def future_decoder_payload():
        return {"format": "black2-battle-runtime-evidence/v1", "read_only": False,
                "mutation_policy": "unsafe", "writes_performed": True}

    monkeypatch.setattr(routes, "_evidence", future_decoder_payload)
    body = api.get("/api/v1/battle/evidence").json()
    assert body["read_only"] is True
    assert body["mutation_policy"] == "cache_reads_only"
    assert body["writes_performed"] is False


def test_candidate_battle_with_generic_dialogue_never_claims_message_phase_or_blocking(api, monkeypatch):
    async def candidate_evidence():
        return {"active": True, "active_status": "candidate", "party_header": {"status": "candidate"}}

    monkeypatch.setattr(routes, "_evidence", candidate_evidence)
    monkeypatch.setattr(routes, "_context", lambda: {
        "is_dialogue_active": True,
        "dialogue_text": "generic printer text",
        "battle_message_overlay": {
            "status": "unresolved",
            "source": "hardware_text_printer_activity_not_battle_message_decoder",
        },
    })

    state = api.get("/api/v1/battle/state").json()
    request = api.get("/api/v1/battle/request").json()

    assert state["active"] is True
    assert state["phase"] == "unresolved"
    assert state["menu"]["status"] == "unresolved"
    assert state["overlays"]["dialogue"]["attribution"] == "unattributed_to_battle"
    assert state["overlays"]["battle_message"]["status"] == "unresolved"
    assert request["phase"] == "unresolved"
    assert request["blocking_overlay"] is None
    assert request["overlay_status"] == "unresolved"


def test_battle_field_uses_gen_v_names_and_unresolved_overworld_context(api, monkeypatch):
    async def candidate_evidence():
        return {"active": True, "active_status": "candidate", "party_header": {"status": "candidate"}}

    monkeypatch.setattr(routes, "_evidence", candidate_evidence)
    monkeypatch.setattr(routes, "_context", lambda: {})

    body = api.get("/api/v1/battle/field").json()
    field = body["field"]

    assert body["battle_field"] == field
    assert "weather" not in field
    assert "terrain" not in field
    assert set(("battle_weather", "field_effects", "side_conditions", "battlefield_visual")) <= set(field)
    assert field["battle_weather"]["status"] == "unresolved"
    assert field["field_effects"]["status"] == "unresolved"
    assert field["side_conditions"]["status"] == "unresolved"
    assert field["battlefield_visual"]["status"] == "unresolved"
    assert field["gen_v_policy"]
    assert set(body["overworld_context"]) >= {"time_of_day", "season", "zone_weather"}
    assert all(body["overworld_context"][key]["status"] == "unresolved"
               for key in ("time_of_day", "season", "zone_weather"))
    assert body["read_only"] is True


def test_party_and_player_zero_moves_expose_only_checksum_validated_persistent_party(api, monkeypatch):
    async def candidate_evidence():
        return {"active": True, "active_status": "candidate", "party_header": {"status": "candidate"}}

    async def candidate_party():
        return {
            "status": "candidate", "capacity": 6, "count": 1,
            "reason": "Persistent player party decoded through GameData.PokeParty; not BattleMon state.",
            "slots": [{
                "slot": 1, "species": 501, "held_item_id": 0, "experience": 229,
                "level": 6, "current_hp": 24, "max_hp": 24, "status_raw": 0,
                "moves": [{"slot": 1, "move_id": 33, "current_pp": 35}],
                "source": "GameData.PokeParty", "integrity": "checksum_verified", "confidence": "candidate",
            }],
        }

    monkeypatch.setattr(routes, "_evidence", candidate_evidence)
    monkeypatch.setattr(routes._party_decoder, "sample", candidate_party)

    party = api.get("/api/v1/battle/party").json()
    moves = api.get("/api/v1/battle/moves?actor=player:0").json()
    other_actor = api.get("/api/v1/battle/moves?actor=player:1").json()

    assert party["contents_known"] is True
    assert party["slots"][0]["integrity"] == "checksum_verified"
    assert party["slots"][0]["confidence"] == "candidate"
    assert party["limitations"]
    assert moves["moves"] == [{"slot": 1, "move_id": 33, "current_pp": 35}]
    assert moves["contents_known"] is True
    assert other_actor["status"] == "unresolved"
    assert other_actor["moves"] == []


def test_action_listing_has_no_executable_actions(api):
    body = api.get("/api/v1/battle/actions").json()
    assert body["status"] == "unresolved"
    assert body["execution_available"] is False
    assert body["available_actions"] == []
    assert all(contract["executable"] is False for contract in body["action_contracts"].values())
    assert body["reason"]["code"] == "BATTLE_RUNTIME_UNRESOLVED"
    assert body["read_only"] is True


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

def test_capture_eval_endpoint_discloses_idle_state(api):
    res = api.get("/api/v1/battle/capture-eval")
    assert res.status_code == 200
    body = res.json()
    assert body["format"] == "black2-battle-capture-eval/v1"
    assert body["active"] is False
    assert body["catchable"] is False
    assert body["recommendation"] == "idle"


def test_battle_items_exposes_categories_and_capture_flag(api):
    res = api.get("/api/v1/battle/items?category=pokeballs")
    assert res.status_code == 200
    body = res.json()
    assert body["format"] == "black2-battle-items/v1"
    assert "pokeballs" in body["categories"]
    assert "capture_allowed" in body


def test_battle_ui_actions_throw_ball_schema_and_rejection_when_inactive(api):
    res = api.post("/api/v1/battle/ui-actions", json={
        "type": "throw_ball",
        "actor": "player:0",
        "item_id": 4,
        "profile": "single_move_grid_v1"
    })
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "rejected"
    assert body["executed"] is False
    assert body["reason"]["code"] == "BATTLE_UI_EXECUTOR_NOT_CONFIGURED"


def test_battle_ui_actions_use_item_schema_and_rejection_when_inactive(api):
    res = api.post("/api/v1/battle/ui-actions", json={
        "type": "use_item",
        "actor": "player:0",
        "item_id": 17,
        "party_slot": 1,
        "profile": "single_move_grid_v1"
    })
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "rejected"
    assert body["executed"] is False
    assert body["reason"]["code"] == "BATTLE_UI_EXECUTOR_NOT_CONFIGURED"
