from pathlib import Path

from backend.black2.decoders.party_runtime import BLOCK_POSITION, _unshuffle_blocks, decode_player_party_from_ram


ROOT = Path(__file__).resolve().parents[1]
TRAINER_MOVE_MENU = ROOT / "reverse_engineering" / "dumps" / (
    "dump_20260908_150407_394928_f6161629_BATTLE_trainer_command_move_menu"
) / "main_ram.bin"
WILD_SAMPLE = ROOT / "reverse_engineering" / "dumps" / (
    "dump_20260907_171504_330411_f5993412_UNIVERSAL_EVIDENCE_战斗中2"
) / "main_ram.bin"


def test_checksum_validated_party_decodes_trainer_move_menu_capture():
    party = decode_player_party_from_ram(TRAINER_MOVE_MENU.read_bytes())

    assert party["status"] == "candidate"
    assert party["capacity"] == 6
    assert party["count"] == 1
    slot = party["slots"][0]
    assert slot["pid"] == "ED18D184"
    assert slot["species"] == 501
    assert slot["level"] == 6
    assert (slot["current_hp"], slot["max_hp"]) == (24, 24)
    assert [(move["move_id"], move["current_pp"]) for move in slot["moves"]] == [
        (33, 35), (39, 30), (0, 0), (0, 0),
    ]
    assert slot["source"] == "GameData.PokeParty"
    assert slot["integrity"] == "checksum_verified"
    assert slot["confidence"] == "candidate"


def test_checksum_validated_party_decodes_wild_battle_sample_without_claiming_battlemon():
    party = decode_player_party_from_ram(WILD_SAMPLE.read_bytes())

    slot = party["slots"][0]
    assert (slot["level"], slot["current_hp"], slot["max_hp"]) == (8, 22, 28)
    assert [(move["move_id"], move["current_pp"]) for move in slot["moves"]] == [
        (33, 30), (39, 30), (55, 25), (0, 0),
    ]
    assert party["reason"].endswith("not BattleMon state.")


def test_corrupt_box_checksum_rejects_entire_party_snapshot():
    ram = bytearray(TRAINER_MOVE_MENU.read_bytes())
    # PokeParty 0x0221E624, header 8, first encrypted word follows Box header.
    ram[0x0221E624 - 0x02000000 + 8 + 8] ^= 0x01

    party = decode_player_party_from_ram(bytes(ram))

    assert party["status"] == "unresolved"
    assert party["slots"] == []
    assert party["reason"] == "Party slot 1 failed Gen V checksum validation."


def test_unshuffle_uses_the_full_gen5_32_selector_table():
    stored = b"".join(bytes([block]) * 32 for block in range(4))

    for selector, order in enumerate(BLOCK_POSITION):
        decoded = _unshuffle_blocks(stored, selector << 13)
        assert decoded == b"".join(bytes([block]) * 32 for block in order)
