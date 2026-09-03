"""Read-only Nintendo DS NitroFS and NARC containers."""
from __future__ import annotations

from pathlib import Path
import struct
from typing import Iterable


class RomFormatError(ValueError):
    """The ROM or archive structure is not valid for this reader."""


class NitroRom:
    """Read named files from a Nintendo DS ROM image."""

    def __init__(self, rom_path: str | Path) -> None:
        self.path = Path(rom_path)
        if not self.path.is_file():
            raise FileNotFoundError(f"ROM not found: {self.path}")
        self._data = self.path.read_bytes()
        if len(self._data) < 0x50:
            raise RomFormatError("File is too short to be a Nintendo DS ROM")
        self._fnt_offset, self._fnt_size = struct.unpack_from("<II", self._data, 0x40)
        self._fat_offset, self._fat_size = struct.unpack_from("<II", self._data, 0x48)
        if not self._in_range(self._fnt_offset, self._fnt_size):
            raise RomFormatError("Nintendo DS FNT points outside the ROM")
        if not self._in_range(self._fat_offset, self._fat_size) or self._fat_size % 8:
            raise RomFormatError("Nintendo DS FAT is invalid")
        self._paths = self._read_paths()

    def _in_range(self, offset: int, size: int) -> bool:
        return 0 <= offset <= len(self._data) and 0 <= size <= len(self._data) - offset

    def _read_paths(self) -> dict[str, int]:
        if self._fnt_size < 8:
            raise RomFormatError("Nintendo DS FNT is truncated")
        directory_count = struct.unpack_from("<H", self._data, self._fnt_offset + 6)[0] & 0x0FFF
        if not directory_count or self._fnt_size < directory_count * 8:
            raise RomFormatError("Nintendo DS FNT directory table is invalid")

        paths: dict[str, int] = {}
        seen: set[int] = set()
        fnt_end = self._fnt_offset + self._fnt_size

        def walk(directory_index: int, prefix: str) -> None:
            if directory_index in seen:
                return
            if directory_index >= directory_count:
                raise RomFormatError("Nintendo DS FNT references an invalid directory")
            seen.add(directory_index)
            table = self._fnt_offset + directory_index * 8
            subtable_rel, file_id, _parent = struct.unpack_from("<IHH", self._data, table)
            cursor = self._fnt_offset + subtable_rel
            if cursor >= fnt_end:
                raise RomFormatError("Nintendo DS FNT subtable is outside the FNT")
            next_file_id = file_id
            while cursor < fnt_end:
                marker = self._data[cursor]
                cursor += 1
                if marker == 0:
                    return
                name_length = marker & 0x7F
                if not name_length or cursor + name_length > fnt_end:
                    raise RomFormatError("Nintendo DS FNT name is invalid")
                name = self._data[cursor:cursor + name_length].decode("ascii")
                cursor += name_length
                path = f"{prefix}/{name}" if prefix else name
                if marker & 0x80:
                    if cursor + 2 > fnt_end:
                        raise RomFormatError("Nintendo DS FNT directory entry is truncated")
                    child_id = struct.unpack_from("<H", self._data, cursor)[0]
                    cursor += 2
                    walk(child_id & 0x0FFF, path)
                else:
                    paths[path] = next_file_id
                    next_file_id += 1
            raise RomFormatError("Nintendo DS FNT subtable has no terminator")

        walk(0, "")
        return paths

    def file_names(self) -> Iterable[str]:
        return self._paths.keys()

    def read_file(self, path: str) -> bytes:
        clean_path = path.strip("/")
        try:
            file_id = self._paths[clean_path]
        except KeyError as error:
            raise FileNotFoundError(f"ROM file not found: /{clean_path}") from error
        fat_entry = self._fat_offset + file_id * 8
        if fat_entry + 8 > self._fat_offset + self._fat_size:
            raise RomFormatError(f"ROM file ID {file_id} is outside the FAT")
        start, end = struct.unpack_from("<II", self._data, fat_entry)
        if start > end or not self._in_range(start, end - start):
            raise RomFormatError(f"ROM file /{clean_path} points outside the ROM")
        return self._data[start:end]


class NarcArchive:
    """Read the BTAF/FIMG entries used by the B2/W2 ROM."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 0x10 or data[:4] != b"NARC":
            raise RomFormatError("Expected a NARC archive")
        header_size = struct.unpack_from("<H", data, 0x0C)[0]
        if header_size < 0x10 or header_size > len(data):
            raise RomFormatError("NARC header size is invalid")
        chunks: dict[bytes, tuple[int, int]] = {}
        cursor = header_size
        while cursor + 8 <= len(data):
            magic = data[cursor:cursor + 4]
            size = struct.unpack_from("<I", data, cursor + 4)[0]
            if size < 8 or cursor + size > len(data):
                raise RomFormatError("NARC chunk size is invalid")
            chunks[magic] = (cursor + 8, size - 8)
            cursor += size
        fat = chunks.get(b"BTAF") or chunks.get(b"FATB")
        image = chunks.get(b"FIMG") or chunks.get(b"GMIF")
        if fat is None or image is None:
            raise RomFormatError("NARC is missing BTAF/FIMG")
        fat_offset, fat_size = fat
        image_offset, image_size = image
        if fat_size < 4:
            raise RomFormatError("NARC BTAF is truncated")
        count = struct.unpack_from("<H", data, fat_offset)[0]
        entries_offset = fat_offset + 4
        if entries_offset + count * 8 > fat_offset + fat_size:
            raise RomFormatError("NARC BTAF entries are truncated")
        self.files = tuple(
            data[image_offset + start:image_offset + end]
            for start, end in (
                struct.unpack_from("<II", data, entries_offset + index * 8)
                for index in range(count)
            )
        )
        if any(start > end or end > image_size for start, end in (
            struct.unpack_from("<II", data, entries_offset + index * 8)
            for index in range(count)
        )):
            raise RomFormatError("NARC file points outside FIMG")
