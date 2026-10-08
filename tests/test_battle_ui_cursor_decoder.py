import asyncio

from backend.black2.decoders.battle_ui_cursor import (
    BATTLE_INPUT_BLOCK_SIZE,
    BattleUiCursorDecoder,
    decode_battle_ui_input_object,
)


def _input_object(*, phase: int = 2, cursor: int = 0x4200, size: int = BATTLE_INPUT_BLOCK_SIZE) -> bytes:
    data = bytearray(BATTLE_INPUT_BLOCK_SIZE)
    data[0:4] = b"\x44\x55\x00\x00"
    data[4:8] = size.to_bytes(4, "little")
    data[0x14:0x14 + len(b"btlv_input.c\x00")] = b"btlv_input.c\x00"
    data[0x84:0x88] = phase.to_bytes(4, "little")
    data[0x94:0x98] = cursor.to_bytes(4, "little")
    return bytes(data)


def test_controlled_move_grid_cursor_mapping_is_decoded_without_guessing_slot_four():
    body = decode_battle_ui_input_object(
        _input_object(),
        header_address=0x022B8880,
        frame=12345,
    )

    assert body["status"] == "candidate"
    assert body["verified"] is False
    assert body["phase"] == {
        "status": "candidate",
        "raw_u32": 2,
        "value": "move_menu",
        "field": {"offset": "+0x84", "address": "0x022B8904"},
    }
    assert body["cursor"]["status"] == "candidate"
    assert body["cursor"]["slot"] == 1
    assert body["cursor"]["grid"] == "top_left"
    assert body["cursor"]["raw_u32"] == 0x4200
    assert body["mapping"]["slot_4"] == "candidate_readback_only_empty_cell"
    assert body["memory"]["cursor_field_address"] == "0x022B8914"


def test_wrong_gfl_size_is_rejected_even_when_source_tag_matches():
    body = decode_battle_ui_input_object(
        _input_object(size=0x154),
        header_address=0x022B8C18,
        frame=12345,
    )

    assert body["status"] == "rejected_candidate"
    assert body["object"]["source_ok"] is True
    assert body["object"]["size_ok"] is False
    assert body["cursor"]["status"] == "unresolved"


def test_semantically_checked_source_variants_share_their_move_slots():
    for raw, slot in ((0x4400, 1), (0x5E00, 1), (0x4620, 2), (0x4260, 4), (0x1E00, 3)):
        body = decode_battle_ui_input_object(
            _input_object(cursor=raw),
            header_address=0x022B8880,
            frame=12345,
        )
        assert body["cursor"]["status"] == "candidate"
        assert body["cursor"]["slot"] == slot


class FakeReader:
    async def scan_pattern_snapshot(self, pattern, *, start, size, limit):
        assert bytes(pattern) == b"btlv_input.c\x00"
        assert start == 0x2B0000
        assert size == 0x10000
        assert limit == 32
        return {"frame": 99, "matches": [0x2B8894, 0x2B8C2C]}

    async def read_batch_snapshot(self, specs):
        assert [spec["id"] for spec in specs] == ["input_022B8880", "input_022B8C18"]
        return {
            "frame": 100,
            "results": {
                "input_022B8880": {"bytes": list(_input_object(cursor=0x4040))},
                "input_022B8C18": {"bytes": list(_input_object(size=0x154))},
            },
        }


def test_live_decoder_filters_child_object_and_returns_same_frame_cursor():
    body = asyncio.run(BattleUiCursorDecoder(FakeReader()).sample())

    assert body["status"] == "candidate"
    assert body["frame"] == 100
    assert body["object"]["header_address"] == "0x022B8880"
    assert body["cursor"]["slot"] == 3
    assert body["cursor"]["raw_u32"] == 0x4040
    assert len(body["candidates"]) == 2
    assert body["candidates"][1]["status"] == "rejected_candidate"
