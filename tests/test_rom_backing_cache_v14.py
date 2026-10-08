"""Offline cache boundaries for immutable NDS ROM resources."""
from __future__ import annotations

import struct
from pathlib import Path

from backend.black2.world import gen5_rom_map
from backend.black2.world.rom_reader import NitroRom


def _narc(payload: bytes) -> bytes:
    btaf = struct.pack("<HHII", 1, 0, 0, len(payload))
    out = bytearray(0x10)
    out[:4] = b"NARC"
    struct.pack_into("<H", out, 0x0C, 0x10)
    for magic, body in ((b"BTAF", btaf), (b"FIMG", payload)):
        out += magic + struct.pack("<I", 8 + len(body)) + body
    return bytes(out)


def _write_nitro_rom(path: Path, files: dict[str, bytes]) -> None:
    names = list(files)
    fnt = bytearray(8)
    struct.pack_into("<IHH", fnt, 0, 8, 0, 1)
    for name in names:
        encoded = name.encode("ascii")
        assert 0 < len(encoded) < 0x80
        fnt.append(len(encoded))
        fnt += encoded
    fnt.append(0)

    fnt_offset = 0x80
    fat_offset = (fnt_offset + len(fnt) + 3) & ~3
    data_offset = (fat_offset + len(names) * 8 + 3) & ~3
    total_size = data_offset + sum(len(payload) for payload in files.values())
    out = bytearray(total_size)
    struct.pack_into("<II", out, 0x40, fnt_offset, len(fnt))
    struct.pack_into("<II", out, 0x48, fat_offset, len(names) * 8)
    out[fnt_offset:fnt_offset + len(fnt)] = fnt

    cursor = data_offset
    for index, payload in enumerate(files.values()):
        out[cursor:cursor + len(payload)] = payload
        struct.pack_into("<II", out, fat_offset + index * 8, cursor, cursor + len(payload))
        cursor += len(payload)
    path.write_bytes(out)


def _fixture(path: Path) -> Path:
    files = {"zone": b"\0" * 0x30, "area": b"\0" * 10}
    files.update({f"arc{index}": _narc(f"payload-{index}".encode("ascii")) for index in range(8)})
    _write_nitro_rom(path, files)
    return path


def test_shared_nitro_backing_and_archive_lru_are_bounded(tmp_path):
    path = _fixture(tmp_path / "shared.nds")
    first = NitroRom.shared(path)
    second = NitroRom.shared(path.parent / "." / path.name)
    try:
        assert first is second
        assert first.read_file("zone") == b"\0" * 0x30
        for index in range(8):
            assert first.archive(f"arc{index}").files == (f"payload-{index}".encode("ascii"),)
        status = first.cache_status()
        assert status["backing"]["mode"] == "read_only_mmap"
        assert status["backing"]["shared"] is True
        assert status["backing"]["mapped_bytes"] == path.stat().st_size
        assert status["archives"]["entries"] <= status["archives"]["entry_cap"]
        assert status["archives"]["bytes"] <= status["archives"]["byte_cap"]
        assert "arc0" not in status["archives"]["paths"]
        assert "arc7" in status["archives"]["paths"]
    finally:
        first.close()


def test_gen5_map_instances_share_the_rom_backing_and_expose_chunk_cap(tmp_path, monkeypatch):
    path = _fixture(tmp_path / "gen5.nds")
    monkeypatch.setattr(gen5_rom_map, "ZONE_DATA_PATH", "zone")
    monkeypatch.setattr(gen5_rom_map, "AREA_DATA_PATH", "area")
    first = gen5_rom_map.Gen5RomMap(path)
    second = gen5_rom_map.Gen5RomMap(str(path))
    try:
        assert first.rom is second.rom
        assert first.archive("arc7") is second.archive("arc7")
        status = first.cache_status()
        assert status["decoded"]["chunk"]["cap"] == gen5_rom_map.CHUNK_CACHE_CAP == 64
        assert status["decoded"]["building_bundle"]["cap"] == gen5_rom_map.BUILDING_BUNDLE_CACHE_CAP == 16
    finally:
        first.rom.close()
