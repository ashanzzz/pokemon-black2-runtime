from pathlib import Path

import pytest

from backend.black2.decoders.trainer_rom import TrainerRomCatalog


ROM = Path(r"D:\game\desmume-0.9.13-win64\口袋妖怪黑2.nds")


@pytest.mark.skipif(not ROM.is_file(), reason="Black 2 ROM is not available in this workspace")
def test_trainer_catalog_resolves_name_class_and_party_from_rom():
    record = TrainerRomCatalog(rom_path=ROM).get(2)

    assert record["status"] == "resolved"
    assert record["trainer_id"] == 2
    assert record["name"] == "丹"
    assert record["trainer_class"]["name"] == "橄榄球选手"
    assert [row["species_id"] for row in record["party"]] == [554, 532]
    assert [row["species"]["name"] for row in record["party"]] == ["Darumaka", "Timburr"]
    assert record["provenance"]["trdata"]["archive"] == "a/0/9/1"
    assert record["provenance"]["trpoke"]["archive"] == "a/0/9/2"


def test_map_script_trainer_flag_normalizes_0x400_encoded_id():
    assert TrainerRomCatalog._normalize_script_trainer_id(0x44D) == (77, "raw_minus_0x400")
    assert TrainerRomCatalog._normalize_script_trainer_id(77) == (77, "direct")
    assert TrainerRomCatalog._normalize_script_trainer_id(0) == (None, "unresolved")


def test_map_script_scanner_extracts_trainer_battle_without_executing_script():
    # One pointer entry at offset 0 points to the command stream at 0x0C;
    # 0xFD13 terminates the header at offset 4.  The command uses the retail
    # 0x400-flagged representation of trainer 77.
    body = bytearray(0x30)
    body[0:4] = (0x08).to_bytes(4, "little")
    body[4:6] = (0xFD13).to_bytes(2, "little")
    body[0x0C:0x16] = bytes.fromhex("94 00 4D 04 03 00 28 00 40 00")

    rows = TrainerRomCatalog._scan_script_trainer_battles(bytes(body))

    assert len(rows) == 1
    assert rows[0]["opcode_name"] == "TrainerBattle"
    assert rows[0]["raw_trainer_id"] == 0x44D
    assert rows[0]["trainer_id"] == 77
    assert rows[0]["id_encoding"] == "raw_minus_0x400"


def test_map_script_scanner_aligns_odd_function_start_to_opcode_boundary():
    # The pointer table may resolve a function to an odd byte offset while
    # the VM command stream remains 16-bit aligned.  The real Zone 446
    # script has this shape; scanning from the raw odd start would skip the
    # TrainerBattle at the following even offset.
    body = bytearray(0x30)
    body[0:4] = (0x09).to_bytes(4, "little")  # 4 + 9 = odd script start 0x0D
    body[4:6] = (0xFD13).to_bytes(2, "little")
    body[0x0E:0x18] = bytes.fromhex("94 00 4D 04 03 00 28 00 40 00")

    rows = TrainerRomCatalog._scan_script_trainer_battles(bytes(body))

    assert len(rows) == 1
    assert rows[0]["offset"] == 0x0E
    assert rows[0]["script_start"] == 0x0D
    assert rows[0]["trainer_id"] == 77


def test_script_word_candidates_preserve_unknown_command_boundary():
    body = bytearray(0x30)
    candidates = TrainerRomCatalog._script_word_candidates(
        bytes.fromhex(
            "3D 00 00 04 76 00 94 00 4D 04 03 00 28 00 40 00"
        ),
        start=0,
        end=16,
        max_words=16,
    )

    assert [row["name"] for row in candidates] == [
        "Message.Actor",
        "TrainerBattle",
    ]
    assert all(row["confidence"] == "script_word_candidate" for row in candidates)
    assert all("command boundary" in row["reason"] for row in candidates)


@pytest.mark.skipif(not ROM.is_file(), reason="Black 2 ROM is not available in this workspace")
def test_zone_script_catalog_binds_zone_427_npc6_without_claiming_execution():
    catalog = TrainerRomCatalog(rom_path=ROM).zone_script_catalog(
        427,
        script_index=11,
        prefix_words=32,
    )

    assert catalog["format"] == "black2-zone-script-catalog/v1"
    assert catalog["status"] == "candidate"
    assert catalog["scripts_id"] == 854
    assert catalog["functions"][0]["script_index"] == 11
    assert catalog["functions"][0]["trainer_battle_candidates"] == []
    assert any(
        row.get("record_index") == 6 and row.get("script_index") == 11
        for row in catalog["bindings"]["npcs"]
    )
    assert any(
        row["name"] == "Message.Actor"
        for row in catalog["functions"][0]["opcode_candidates"]
    )
    assert catalog["policy"]["execution"].startswith("static ROM only")
