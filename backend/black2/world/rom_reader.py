"""Read-only Nintendo DS NitroFS and NARC containers.

Compatibility copy included in the v5 overlay so the static ROM layer is
self-contained.  It preserves the v4 public interface used by map modules.
"""
from __future__ import annotations

from collections import OrderedDict
import mmap
from pathlib import Path
import struct
import threading
from typing import Iterable
from weakref import WeakValueDictionary


class RomFormatError(ValueError):
    """The ROM or archive structure is not valid for this reader."""


class NitroRom:
    """Read named files from a Nintendo DS ROM image."""

    # A B2/W2 ROM is 512 MiB.  Several independent API services create ROM
    # readers, so they must share one immutable backing instead of each
    # retaining another whole-file bytes object.
    ARCHIVE_CACHE_CAP = 6
    ARCHIVE_CACHE_BYTE_CAP = 96 * 1024 * 1024
    _shared_lock = threading.RLock()
    _shared_backings: WeakValueDictionary[tuple[str, int, int], "NitroRom"] = WeakValueDictionary()

    def __init__(self, rom_path: str | Path) -> None:
        self.path = Path(rom_path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"ROM not found: {self.path}")
        self._shared_key: tuple[str, int, int] | None = None
        self._archive_lock = threading.RLock()
        self._archive_cache: OrderedDict[str, NarcArchive] = OrderedDict()
        self._archive_cache_bytes = 0
        self._file = self.path.open("rb")
        try:
            self._data = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        except Exception:
            self._file.close()
            raise
        try:
            if len(self._data) < 0x50:
                raise RomFormatError("File is too short to be a Nintendo DS ROM")
            self._fnt_offset, self._fnt_size = struct.unpack_from("<II", self._data, 0x40)
            self._fat_offset, self._fat_size = struct.unpack_from("<II", self._data, 0x48)
            if not self._in_range(self._fnt_offset, self._fnt_size):
                raise RomFormatError("Nintendo DS FNT points outside the ROM")
            if not self._in_range(self._fat_offset, self._fat_size) or self._fat_size % 8:
                raise RomFormatError("Nintendo DS FAT is invalid")
            self._paths = self._read_paths()
        except Exception:
            self._data.close()
            self._file.close()
            raise

    @classmethod
    def shared(cls, rom_path: str | Path) -> "NitroRom":
        """Return the process-wide immutable backing for one ROM revision."""
        path = Path(rom_path).resolve()
        stat = path.stat()
        key = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
        with cls._shared_lock:
            existing = cls._shared_backings.get(key)
            if existing is not None:
                return existing
            created = cls(path)
            created._shared_key = key
            cls._shared_backings[key] = created
            return created

    def close(self) -> None:
        """Release a backing explicitly; normal services keep it for their lifetime."""
        with self._archive_lock:
            self._archive_cache.clear()
            self._archive_cache_bytes = 0
        try:
            if not self._data.closed:
                self._data.close()
        finally:
            if not self._file.closed:
                self._file.close()
        key = self._shared_key
        if key is not None:
            with type(self)._shared_lock:
                if type(self)._shared_backings.get(key) is self:
                    del type(self)._shared_backings[key]

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

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

    def archive(self, path: str) -> "NarcArchive":
        """Load a NARC lazily into a small shared LRU for this ROM backing."""
        clean_path = path.strip("/")
        with self._archive_lock:
            cached = self._archive_cache.get(clean_path)
            if cached is not None:
                self._archive_cache.move_to_end(clean_path)
                return cached

        decoded = NarcArchive(self.read_file(clean_path))
        with self._archive_lock:
            cached = self._archive_cache.get(clean_path)
            if cached is not None:
                self._archive_cache.move_to_end(clean_path)
                return cached
            self._archive_cache[clean_path] = decoded
            self._archive_cache.move_to_end(clean_path)
            self._archive_cache_bytes += decoded.byte_size
            while (
                len(self._archive_cache) > self.ARCHIVE_CACHE_CAP
                or (
                    self._archive_cache_bytes > self.ARCHIVE_CACHE_BYTE_CAP
                    and len(self._archive_cache) > 1
                )
            ):
                _path, evicted = self._archive_cache.popitem(last=False)
                self._archive_cache_bytes -= evicted.byte_size
            return decoded

    def cache_status(self) -> dict[str, object]:
        with self._archive_lock:
            archive = {
                "entries": len(self._archive_cache),
                "entry_cap": self.ARCHIVE_CACHE_CAP,
                "bytes": self._archive_cache_bytes,
                "byte_cap": self.ARCHIVE_CACHE_BYTE_CAP,
                "paths": list(self._archive_cache),
            }
        with type(self)._shared_lock:
            shared_backing_count = len(type(self)._shared_backings)
        return {
            "format": "nitro-rom-cache-status/v1",
            "backing": {
                "mode": "read_only_mmap",
                "path": str(self.path),
                "mapped_bytes": len(self._data),
                "shared": self._shared_key is not None,
                "active_shared_backings": shared_backing_count,
            },
            "archives": archive,
        }


class NarcArchive:
    """Read the BTAF/FIMG entries used by B2/W2."""

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
        entries = [
            struct.unpack_from("<II", data, entries_offset + index * 8)
            for index in range(count)
        ]
        if any(start > end or end > image_size for start, end in entries):
            raise RomFormatError("NARC file points outside FIMG")
        self.files = tuple(data[image_offset + start:image_offset + end] for start, end in entries)
        # The source buffer is discarded after parsing.  This is the retained
        # payload size used by NitroRom's bounded archive cache.
        self.byte_size = sum(len(item) for item in self.files)
