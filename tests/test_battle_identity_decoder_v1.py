from backend.black2.decoders.battle_identity import (
    decode_battle_poke_candidates_from_ram,
    decode_battle_trainer_text_candidates_from_ram,
)


def _battle_poke_block(*, species_id: int, current_hp: int, max_hp: int) -> bytes:
    data = bytearray(0x300)
    data[0:4] = b"\x44\x55\x00\x00"
    data[4:8] = (0x214).to_bytes(4, "little")
    data[0x14:0x24] = b"btl_pokeparam.c\x00"
    payload = 0x20
    data[payload + 0x14:payload + 0x18] = (123).to_bytes(4, "little")
    data[payload + 0x18:payload + 0x1A] = species_id.to_bytes(2, "little")
    data[payload + 0x1A:payload + 0x1C] = max_hp.to_bytes(2, "little")
    data[payload + 0x1C:payload + 0x1E] = current_hp.to_bytes(2, "little")
    return bytes(data)


def test_battle_pokeparam_species_is_decoded_from_ram_and_mapped_to_dex():
    body = decode_battle_poke_candidates_from_ram(
        _battle_poke_block(species_id=504, current_hp=14, max_hp=14),
        base_address=0x0225B000,
        frame=1234,
    )

    assert body["status"] == "candidate"
    assert body["verified"] is False
    assert body["species_candidates"][0]["species_id"] == 504
    assert body["species_candidates"][0]["species"]["name"] == "Patrat"
    assert body["blocks"][0]["payload_address"] == "0x0225B020"
    assert body["blocks"][0]["raw"]["ram_window"]["frame"] == 1234


def test_battle_trainer_name_is_decoded_from_adjacent_setup_and_strbuf_blocks():
    data = bytearray(0x180)
    setup = 0x00
    text = 0x54
    for offset, source, size in (
        (setup, b"btl_setup.c\x00", 0x44),
        (text, b"strbuf.c\x00", 0x48),
    ):
        data[offset:offset + 4] = b"\x44\x55\x00\x00"
        data[offset + 4:offset + 8] = size.to_bytes(4, "little")
        data[offset + 0x14:offset + 0x14 + len(source)] = source

    payload = text + 0x20
    words = (0x6B21, 0x90CE, 0xFFFF)  # 次郎
    for index, word in enumerate(words):
        start = payload + 0x14 + index * 2
        data[start:start + 2] = word.to_bytes(2, "little")

    body = decode_battle_trainer_text_candidates_from_ram(
        bytes(data),
        base_address=0x0224B000,
        frame=4321,
    )

    assert body["status"] == "candidate"
    assert body["verified"] is False
    assert body["text_candidates"][0]["text"] == "次郎"
    assert body["text_candidates"][0]["address"] == "0x0224B088"
    assert body["text_candidates"][0]["setup_association"]["status"] == "candidate"
