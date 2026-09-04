"""B2/W2 ROM map matrices and verified collision dimensions."""
from __future__ import annotations

from dataclasses import dataclass
import os
import struct
from typing import Optional

from .rom_reader import NarcArchive, NitroRom


DEFAULT_ROM_PATHS = (
    r"D:\SynologyDrive\download\desmume-0.9.13-win64\口袋妖怪黑2.nds",
    r"D:\口袋妖怪黑2.nds",
)
_ONE_PERMISSION_BLOCK = 0x00034257
_TWO_PERMISSION_BLOCKS = 0x00044347


@dataclass(frozen=True)
class PermissionModel:
    model_id: int
    width: int
    height: int
    signature: int = 0
    planes: tuple[tuple[int, ...], ...] = ()

    @property
    def plane_count(self) -> int:
        return len(self.planes)

    def plane_rows(self, plane: int) -> list[list[int]]:
        values = self.planes[plane]
        return [
            list(values[row * self.width:(row + 1) * self.width])
            for row in range(self.height)
        ]


class NativeMapEngine:
    """Cached ROM reader shared by the native visual map endpoints."""

    _instance: Optional["NativeMapEngine"] = None

    def __init__(self, rom_path: str | None = None) -> None:
        candidates = (rom_path,) if rom_path else ()
        candidates += (os.getenv("BLACK2_ROM_PATH"),) + DEFAULT_ROM_PATHS
        selected = next((path for path in candidates if path and os.path.isfile(path)), None)
        if not selected:
            raise FileNotFoundError(f"ROM not found in paths: {candidates}")
        self.rom = NitroRom(selected)
        self.zone_data = self.rom.read_file("a/0/1/2")
        self.matrix_narc = NarcArchive(self.rom.read_file("a/0/0/9"))
        self.model_narc = NarcArchive(self.rom.read_file("a/0/0/8"))
        self.models = {
            model_id: model
            for model_id, payload in enumerate(self.model_narc.files)
            if (model := self._decode_model(model_id, payload)) is not None
        }

    @classmethod
    def get_instance(cls) -> "NativeMapEngine":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _decode_model(model_id: int, payload: bytes) -> PermissionModel | None:
        if len(payload) < 16:
            return None
        signature, _bmd0_offset, collision_offset, _total_size = struct.unpack_from("<4I", payload)
        if signature not in {_ONE_PERMISSION_BLOCK, _TWO_PERMISSION_BLOCKS}:
            return None
        if collision_offset + 4 > len(payload):
            return None
        width, height = struct.unpack_from("<HH", payload, collision_offset)
        plane_count = 8 if signature == _ONE_PERMISSION_BLOCK else 16
        if not (1 <= width <= 512 and 1 <= height <= 512):
            return None
        if collision_offset + 4 + width * height * plane_count > len(payload):
            return None
        cells = width * height
        data_offset = collision_offset + 4
        data_end = data_offset + cells * plane_count
        if data_end > len(payload):
            return None
        planes = tuple(
            tuple(payload[data_offset + plane * cells:data_offset + (plane + 1) * cells])
            for plane in range(plane_count)
        )
        return PermissionModel(model_id, width, height, signature, planes)

    def matrix_for_map(self, map_id: int):
        zone_offset = map_id * 48
        if zone_offset < 0 or zone_offset + 48 > len(self.zone_data):
            raise ValueError(f"ZoneData entry {map_id} is outside the ROM table")
        matrix_id = struct.unpack_from("<H", self.zone_data, zone_offset + 4)[0]  # v5: ZoneHeader.matrixID
        raw = self.matrix_narc.files[matrix_id]
        width, height = struct.unpack_from("<HH", raw, 4)
        count = width * height
        if not width or not height or len(raw) < 8 + count * 4:
            raise ValueError(f"Matrix {matrix_id} has invalid dimensions")
        model_ids = struct.unpack_from(f"<{count}I", raw, 8)
        has_zones = struct.unpack_from("<I", raw, 0)[0] == 1
        definitions = (  # deprecated name: this is the matrix ZoneID table
            struct.unpack_from(f"<{count}I", raw, 8 + count * 4)
            if has_zones and len(raw) >= 8 + count * 8
            else None
        )
        return matrix_id, width, height, model_ids, definitions
