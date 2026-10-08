"""Unit tests for Party Swap Order API."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from unittest.mock import AsyncMock, patch

from backend.black2.api.pc_routes import router, post_party_swap, PartySwapOrderRequest, post_party_teach_move, PartyTeachMoveRequest


@pytest.fixture
def api() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    @app.post("/api/v1/game/party/swap")
    async def _swap(req: PartySwapOrderRequest):
        return await post_party_swap(req)
    @app.post("/api/v1/game/party/teach-move")
    async def _teach(req: PartyTeachMoveRequest):
        return await post_party_teach_move(req)
    return TestClient(app)


def test_party_swap_identical_slots_is_noop(api):
    res = api.post("/api/v1/game/party/swap", json={"slot_a": 1, "slot_b": 1})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["action"] == "swap_party_order"
    assert "no-op" in body["message"]


def test_party_swap_validates_slot_range(api):
    res = api.post("/api/v1/game/party/swap", json={"slot_a": 0, "slot_b": 2})
    assert res.status_code == 422
    res2 = api.post("/api/v1/game/party/swap", json={"slot_a": 1, "slot_b": 7})
    assert res2.status_code == 422


def test_party_swap_executes_memory_swap(api):
    with patch("backend.black2.api.pc_routes._get_save_base_and_party_ptr", new_callable=AsyncMock) as mock_ptrs, \
         patch("backend.black2.api.pc_routes._read_main_ram", new_callable=AsyncMock) as mock_read, \
         patch("backend.black2.api.pc_routes._write_main_ram", new_callable=AsyncMock) as mock_write, \
         patch("backend.black2.api.battle_routes._party_decoder.sample", new_callable=AsyncMock) as mock_sample:

        party_ptr = 0x02250000
        save_base = 0x02200000
        mock_ptrs.return_value = (save_base, party_ptr)

        party_raw = bytearray(8 + 6 * 220)
        party_raw[0:4] = (6).to_bytes(4, "little")
        party_raw[4:8] = (6).to_bytes(4, "little")
        party_raw[8:8+4] = (0x11111111).to_bytes(4, "little")
        party_raw[8+220:8+220+4] = (0x22222222).to_bytes(4, "little")

        mock_read.return_value = bytes(party_raw)
        mock_write.return_value = {"ok": True}
        mock_sample.return_value = {"format": "black2-player-party/v1", "status": "candidate", "count": 6, "slots": []}

        res = api.post("/api/v1/game/party/swap", json={"slot_a": 1, "slot_b": 2})
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["action"] == "swap_party_order"
        assert "swapped_a" in body
        assert "swapped_b" in body
        assert "latest_lineup" in body
        assert "summary_zh" in body
        assert mock_write.call_count == 2


def test_party_teach_move_validates_slot_range(api):
    res = api.post("/api/v1/game/party/teach-move", json={"party_slot": 0, "move_slot": 1, "move_id": 82})
    assert res.status_code == 422
    res2 = api.post("/api/v1/game/party/teach-move", json={"party_slot": 1, "move_slot": 5, "move_id": 82})
    assert res2.status_code == 422


def test_party_teach_move_executes_reencryption_and_write(api):
    with patch("backend.black2.api.pc_routes._get_save_base_and_party_ptr", new_callable=AsyncMock) as mock_ptrs, \
         patch("backend.black2.api.pc_routes._read_main_ram", new_callable=AsyncMock) as mock_read, \
         patch("backend.black2.api.pc_routes._write_main_ram", new_callable=AsyncMock) as mock_write, \
         patch("backend.black2.api.battle_routes._party_decoder.sample", new_callable=AsyncMock) as mock_sample:
        party_ptr = 0x02250000
        save_base = 0x02200000
        mock_ptrs.return_value = (save_base, party_ptr)
        party_raw = bytearray(8 + 6 * 220)
        party_raw[0:4] = (6).to_bytes(4, "little")
        party_raw[4:8] = (6).to_bytes(4, "little")
        party_raw[8:8+4] = (0x12345678).to_bytes(4, "little")
        mock_read.return_value = bytes(party_raw)
        mock_write.return_value = {"ok": True}
        mock_sample.return_value = {"format": "black2-player-party/v1", "status": "candidate", "count": 6, "slots": []}
        res = api.post("/api/v1/game/party/teach-move", json={"party_slot": 1, "move_slot": 1, "move_id": 82, "pp": 10})
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["action"] == "teach_move"
        assert body["new_move_id"] == 82
        assert body["pp"] == 10
        assert "pokemon" in body
        assert "old_move" in body
        assert "new_move" in body
        assert "current_moves" in body
        assert "summary_zh" in body
        assert "checksum" in body
        assert mock_write.call_count == 1
