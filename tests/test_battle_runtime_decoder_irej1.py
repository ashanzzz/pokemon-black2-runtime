import asyncio
import pytest

from backend.black2.decoders.battle_runtime import (
    BattleRuntimeDecoder,
    FIELD_STATUS_BUSY_FLAG,
    IREJ_REV1_GAME_DATA,
)


def row(data: bytes):
    return {"bytes": list(data)}


class FakeReader:
    def __init__(self, busy=1, capacity=6, count=1):
        self.calls = 0
        self.busy = busy
        self.capacity = capacity
        self.count = count

    async def read_batch_snapshot(self, specs):
        self.calls += 1
        if self.calls == 1:
            return {
                "frame": 100,
                "results": {
                    "party_ptr": row((0x0221E624).to_bytes(4, "little")),
                    "field_status_ptr": row((0x0224211C).to_bytes(4, "little")),
                    "last_battle_result": row((1).to_bytes(4, "little")),
                    "pause_events": row(b"\x00"),
                },
            }
        fs = bytearray(0x18)
        fs[FIELD_STATUS_BUSY_FLAG] = self.busy
        party = self.capacity.to_bytes(4, "little") + self.count.to_bytes(4, "little")
        return {"frame": 101, "results": {"field_status": row(fs), "party_header": row(party)}}


def test_supplied_irej_layout_detects_candidate_battle_without_claiming_verified():
    payload = asyncio.run(BattleRuntimeDecoder(FakeReader()).sample())
    assert payload["active"] is True
    assert payload["active_status"] == "candidate"
    assert payload["verified"] is False
    assert payload["confidence"] == pytest.approx(0.95)
    assert payload["pointers"]["game_data"] == f"0x{IREJ_REV1_GAME_DATA:08X}"
    assert payload["party_header"] == {"status": "candidate", "capacity": 6, "count": 1}
    assert payload["field_busy"] == {"raw": 1, "name": "battle"}


def test_bad_party_header_rejects_candidate_chain():
    payload = asyncio.run(BattleRuntimeDecoder(FakeReader(capacity=99, count=99)).sample())
    assert payload["active"] is None
    assert payload["status"] == "rejected_candidate"
    assert payload["verified"] is False


def test_busy_none_is_only_candidate_negative_until_control_capture_exists():
    payload = asyncio.run(BattleRuntimeDecoder(FakeReader(busy=0)).sample())
    assert payload["active"] is False
    assert payload["active_status"] == "candidate"
    assert payload["verified"] is False
