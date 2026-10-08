"""Public HTTP contract for path-indexed navigation hazards."""
from __future__ import annotations

from tests.test_global_navigation_api_v13 import _client


def test_plan_api_exposes_static_trainer_hazard_without_runtime_promotion():
    client = _client()
    response = client.post("/api/v1/navigation/plans", json={
        "destination": {
            "type": "global_grid",
            "space": "gen5-matrix-grid-v1",
            "x": 34,
            "y": 0,
            "z": 5,
        },
        "constraints": [{
            "constraint_id": "trainer_sight:rom:4",
            "kind": "trainer_sight",
            "behavior": "soft_cost",
            "tiles": [{"zone_id": 10, "x": 31, "y": 0, "z": 5}],
            "cost": 10.0,
            "dynamic": False,
            "confidence": "rom_record",
            "source": "rom:/a/1/2/6",
            "status": "candidate",
            "metadata": {"trainer_id": "rom-npc-4", "sight_raw": 5},
        }],
    })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["route_hazard_summary"]["total"] == 1
    hazard = payload["route_hazards"][0]
    assert set(hazard) >= {
        "constraint_id", "kind", "tiles", "source", "confidence", "status", "metadata",
        "knowledge_state", "trigger_condition", "interruption_policy",
        "route_indices", "first_route_index",
    }
    assert hazard["constraint_id"] == "trainer_sight:rom:4"
    assert hazard["tiles"] == [{"zone_id": 10, "x": 31, "y": 0, "z": 5}]
    assert hazard["knowledge_state"] == "static_candidate"
    assert hazard["trigger_condition"] == {
        "event": "enter_trainer_sight_tile",
        "required_state": "the trainer sight-line is active when the player enters the tile",
        "status": "candidate",
        "runtime_confirmation_required": True,
        "trainer_id": "rom-npc-4",
    }
    assert hazard["interruption_policy"] == "avoid"
    assert hazard["route_indices"] == [1]
    assert hazard["first_route_index"] == 1
    assert payload["route_hazard_decisions"] == [{
        "constraint_id": "trainer_sight:rom:4",
        "first_route_index": 1,
        "route_indices": [1],
        "policy_key": "trainer_sight",
        "policy_value": "soft_avoid",
        "effective_behavior": "soft_cost",
        "effective_cost": 10.0,
        "interruption_policy": "avoid",
    }]


def test_plan_api_hazard_honors_an_explicit_allow_policy():
    client = _client()
    response = client.post("/api/v1/navigation/plans", json={
        "destination": {
            "type": "global_grid",
            "space": "gen5-matrix-grid-v1",
            "x": 34,
            "y": 0,
            "z": 5,
        },
        "policy": {"script_trigger_unknown": "allow"},
        "constraints": [{
            "constraint_id": "trigger:rom:7",
            "kind": "script_trigger",
            "behavior": "unknown",
            "tiles": [{"zone_id": 10, "x": 31, "y": 0, "z": 5}],
            "cost": 25.0,
            "dynamic": False,
            "confidence": "rom_record",
            "source": "rom:/a/1/2/6",
            "status": "activation_unresolved",
            "metadata": {"entity_id": 7},
        }],
    })

    assert response.status_code == 200, response.text
    hazard = response.json()["route_hazards"][0]
    assert hazard["interruption_policy"] == "allow"
    assert hazard["decision"] == {
        "policy_key": "script_trigger_unknown",
        "policy_value": "allow",
        "effective_behavior": "warning",
        "effective_cost": None,
        "interruption_policy": "allow",
    }
