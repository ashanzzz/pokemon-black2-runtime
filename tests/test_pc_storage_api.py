import pytest
from fastapi.testclient import TestClient

from backend.black2.api.app import app
from backend.black2.decoders.pc_storage_runtime import (
    BOX_COUNT,
    BOX_CAPACITY,
    calculate_level_from_exp,
    get_nature_modifiers,
)


@pytest.fixture
def client():
    return TestClient(app)


def test_level_calculation():
    # Level 1 minimum
    assert calculate_level_from_exp(0) == 1
    # Level 5 approx 125 exp
    assert calculate_level_from_exp(125) == 5
    # Level 100 max
    assert calculate_level_from_exp(2000000) == 100


def test_nature_modifiers():
    # Lonely: +Atk, -Def
    inc, dec = get_nature_modifiers(1)
    assert inc == 0  # Atk
    assert dec == 1  # Def

    # Hardy: Neutral
    inc, dec = get_nature_modifiers(0)
    assert inc is None
    assert dec is None


def test_pc_summary_endpoint_structure(client):
    res = client.get("/api/v1/pokemon/pc/summary")
    # Even if live bridge is disconnected in pure offline unit test, schema or 503 is cleanly returned
    assert res.status_code in (200, 503)
    if res.status_code == 200:
        data = res.json()
        assert data["total_boxes"] == BOX_COUNT
        assert data["total_capacity"] == BOX_COUNT * BOX_CAPACITY
        assert len(data["boxes"]) == BOX_COUNT


def test_pc_box_slot_validation(client):
    # Invalid box id
    res = client.get("/api/v1/pokemon/pc/box/99")
    assert res.status_code == 400

    # Invalid slot id
    res = client.get("/api/v1/pokemon/pc/box/1/slot/99")
    assert res.status_code == 400


def test_pc_deposit_validation(client):
    # Invalid party slot
    res = client.post("/api/v1/pokemon/pc/deposit", json={"party_slot": 99})
    assert res.status_code == 422
