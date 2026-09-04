"""Strict Pokémon Black 2 / White 2 Gen-5 ROM map format reader (v5).

This module is deliberately *static-resource only*.  It never reads emulator
RAM and it never guesses the current map.  Runtime code must first resolve the
current Field/Player/Mapper object graph, then join that identity to the static
objects decoded here.

Format layout is aligned with CTRMapV's Gen-5 readers:
- ZoneData record: 0x30 bytes (areaID +0x02, matrixID +0x04, entitiesID +0x16)
- AreaData record: 10 bytes
- Matrix: u32 hasZones, u16 width, u16 height, u32 chunkIds[], optional zoneIds[]
- Map chunk: Game Freak container, file 0 terrain NSBMD, last file ChunkBuildings
- ChunkBuilding: vec3 FX32 + angle16 + modelUID16
- Area building bundle: first half metadata, second half matching NSBMD models

No semantic meaning is assigned to permission-plane bytes here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
import os
import struct
from typing import Any, Iterable, Iterator

from .rom_reader import NarcArchive, NitroRom, RomFormatError


# B2/W2 NARC/file paths (archive IDs converted to a/x/y/z).
MAP_CHUNKS_PATH = "a/0/0/8"
MAP_MATRIX_PATH = "a/0/0/9"
ZONE_DATA_PATH = "a/0/1/2"
AREA_DATA_PATH = "a/0/1/3"
MAP_TEXTURE_PATH = "a/0/1/4"
ZONE_ENTITIES_PATH = "a/1/2/6"
BUILDING_TEXTURE_EXT_PATH = "a/1/7/4"
BUILDING_TEXTURE_INT_PATH = "a/1/7/5"
BUILDING_BUNDLE_EXT_PATH = "a/2/2/5"
BUILDING_BUNDLE_INT_PATH = "a/2/2/6"

ZONE_RECORD_SIZE = 0x30
AREA_RECORD_SIZE = 10
MATRIX_NONE = 0xFFFFFFFF
FX32_ONE = 4096.0
ANGLE16_FULL = 65536.0

# Existing project evidence for map-chunk permission container variants.
ONE_PERMISSION_BLOCK = 0x00034257   # bytes: WB 03 00 ...
TWO_PERMISSION_BLOCKS = 0x00044347  # retained for compatibility/evidence scans


class Gen5MapFormatError(ValueError):
    """A static Gen-5 map object is malformed or inconsistent."""


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _s16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<h", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _s32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _require(data: bytes, size: int, what: str) -> None:
    if len(data) < size:
        raise Gen5MapFormatError(f"{what} is truncated: expected >= {size}, got {len(data)}")


@dataclass(frozen=True)
class ZoneHeader:
    zone_id: int
    map_type: int
    npc_info_cache_idx: int
    area_id: int
    matrix_id: int
    scripts_id: int
    init_scripts_id: int
    text_file_id: int
    bgm_spring: int
    bgm_summer: int
    bgm_autumn: int
    bgm_winter: int
    encounter_id: int
    encounter_unknown_bits: int
    entities_id: int
    parent_zone_id: int
    location_name_id: int
    location_name_display_type: int
    weather: int
    actor_projection_matrix_type: int
    camera_index: int
    map_transition_effect: int
    battle_bg: int
    enable_cycling: bool
    enable_running: bool
    enable_escape_rope: bool
    enable_fly_from: bool
    enable_cycling_bgm: bool
    enable_entralink_warp: bool
    matrix_camera_boundary_index: int
    name_icon: int
    difficulty_level_adjustment: int
    fly_x: int
    fly_y: int
    fly_z: int
    raw_hex: str

    @classmethod
    def parse(cls, raw: bytes, zone_id: int) -> "ZoneHeader":
        _require(raw, ZONE_RECORD_SIZE, f"ZoneHeader[{zone_id}]")
        raw = raw[:ZONE_RECORD_SIZE]
        encounter_raw = _u16(raw, 0x14)
        loc_raw = _u16(raw, 0x1A)
        env_flags = _u16(raw, 0x1C)
        flags_battle = _u16(raw, 0x1E)
        icon_raw = _u16(raw, 0x22)
        return cls(
            zone_id=zone_id,
            map_type=raw[0x00],
            npc_info_cache_idx=raw[0x01],
            area_id=_u16(raw, 0x02),
            matrix_id=_u16(raw, 0x04),
            scripts_id=_u16(raw, 0x06),
            init_scripts_id=_u16(raw, 0x08),
            text_file_id=_u16(raw, 0x0A),
            bgm_spring=_u16(raw, 0x0C),
            bgm_summer=_u16(raw, 0x0E),
            bgm_autumn=_u16(raw, 0x10),
            bgm_winter=_u16(raw, 0x12),
            encounter_id=encounter_raw & 0x1FFF,
            encounter_unknown_bits=(encounter_raw >> 13) & 0x7,
            entities_id=_u16(raw, 0x16),
            parent_zone_id=_u16(raw, 0x18),
            location_name_id=loc_raw & 0x03FF,
            location_name_display_type=(loc_raw >> 10) & 0x3F,
            weather=env_flags & 0x3F,
            actor_projection_matrix_type=(env_flags >> 6) & 0x7,
            camera_index=(env_flags >> 9) & 0x7F,
            map_transition_effect=flags_battle & 0x1F,
            battle_bg=(flags_battle >> 5) & 0x1F,
            enable_cycling=bool(flags_battle & (1 << 10)),
            enable_running=bool(flags_battle & (1 << 11)),
            enable_escape_rope=bool(flags_battle & (1 << 12)),
            enable_fly_from=bool(flags_battle & (1 << 13)),
            enable_cycling_bgm=bool(flags_battle & (1 << 14)),
            enable_entralink_warp=bool(flags_battle & (1 << 15)),
            matrix_camera_boundary_index=_s16(raw, 0x20),
            name_icon=icon_raw & 0x1FFF,
            difficulty_level_adjustment=(icon_raw >> 13) & 0x7,
            fly_x=_s32(raw, 0x24),
            fly_y=_s32(raw, 0x28),
            fly_z=_s32(raw, 0x2C),
            raw_hex=raw.hex(),
        )


@dataclass(frozen=True)
class AreaHeader:
    area_id: int
    buildings_id: int
    textures_id: int
    srt_animation_id: int
    pattern_animation_id: int
    is_exterior: bool
    light_index: int
    outline_type: int
    unknown3: int
    raw_hex: str

    @classmethod
    def parse(cls, raw: bytes, area_id: int) -> "AreaHeader":
        _require(raw, AREA_RECORD_SIZE, f"AreaHeader[{area_id}]")
        raw = raw[:AREA_RECORD_SIZE]
        return cls(
            area_id=area_id,
            buildings_id=_u16(raw, 0x00),
            textures_id=_u16(raw, 0x02),
            srt_animation_id=raw[0x04],
            pattern_animation_id=raw[0x05],
            is_exterior=bool(raw[0x06]),
            light_index=raw[0x07],
            outline_type=raw[0x08],
            unknown3=raw[0x09],
            raw_hex=raw.hex(),
        )


@dataclass(frozen=True)
class MapMatrix:
    matrix_id: int
    has_zones: bool
    width: int
    height: int
    chunk_ids: tuple[int, ...]
    zone_ids: tuple[int, ...] | None
    trailing_bytes: int = 0

    @property
    def cell_count(self) -> int:
        return self.width * self.height

    def cell(self, x: int, y: int) -> dict[str, int | None]:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError((x, y))
        index = y * self.width + x
        return {
            "index": index,
            "x": x,
            "y": y,
            "chunk_id": self.chunk_ids[index],
            "zone_id": self.zone_ids[index] if self.zone_ids is not None else None,
        }

    def cells(self) -> Iterator[dict[str, int | None]]:
        for index, chunk_id in enumerate(self.chunk_ids):
            yield {
                "index": index,
                "x": index % self.width,
                "y": index // self.width,
                "chunk_id": chunk_id,
                "zone_id": self.zone_ids[index] if self.zone_ids is not None else None,
            }

    @classmethod
    def parse(cls, raw: bytes, matrix_id: int) -> "MapMatrix":
        _require(raw, 8, f"MapMatrix[{matrix_id}]")
        has_zones_raw = _u32(raw, 0x00)
        if has_zones_raw not in (0, 1):
            raise Gen5MapFormatError(
                f"MapMatrix[{matrix_id}] hasZones must be 0/1, got 0x{has_zones_raw:08X}"
            )
        width, height = struct.unpack_from("<HH", raw, 0x04)
        if not (1 <= width <= 512 and 1 <= height <= 512):
            raise Gen5MapFormatError(f"MapMatrix[{matrix_id}] invalid dimensions {width}x{height}")
        count = width * height
        cursor = 8
        need = cursor + count * 4
        _require(raw, need, f"MapMatrix[{matrix_id}] chunk table")
        chunks = struct.unpack_from(f"<{count}I", raw, cursor)
        cursor = need
        zone_ids: tuple[int, ...] | None = None
        if has_zones_raw:
            need = cursor + count * 4
            _require(raw, need, f"MapMatrix[{matrix_id}] zone table")
            zone_ids = tuple(struct.unpack_from(f"<{count}I", raw, cursor))
            cursor = need
        return cls(
            matrix_id=matrix_id,
            has_zones=bool(has_zones_raw),
            width=width,
            height=height,
            chunk_ids=tuple(chunks),
            zone_ids=zone_ids,
            trailing_bytes=max(0, len(raw) - cursor),
        )


@dataclass(frozen=True)
class GFContainer:
    magic: str
    file_count: int
    offsets: tuple[int, ...]
    files: tuple[bytes, ...]
    padded: bool
    raw_size: int

    @classmethod
    def parse(cls, raw: bytes, *, expected_magic: str | None = None) -> "GFContainer":
        _require(raw, 8, "Game Freak container")
        try:
            magic = raw[:2].decode("ascii")
        except UnicodeDecodeError as error:
            raise Gen5MapFormatError("Game Freak container has non-ASCII magic") from error
        if not all(ch.isupper() or ch.isdigit() for ch in magic):
            raise Gen5MapFormatError(f"invalid Game Freak container magic {magic!r}")
        if expected_magic is not None and magic != expected_magic:
            raise Gen5MapFormatError(f"expected {expected_magic} container, got {magic}")
        file_count = _u16(raw, 2)
        if file_count > 4096:
            raise Gen5MapFormatError(f"implausible Game Freak file count {file_count}")
        table_end = 4 + (file_count + 1) * 4
        _require(raw, table_end, f"{magic} offset table")
        offsets = tuple(struct.unpack_from(f"<{file_count + 1}I", raw, 4))
        if any(offset < table_end or offset > len(raw) for offset in offsets):
            raise Gen5MapFormatError(f"{magic} contains an out-of-range file offset")
        if any(a > b for a, b in zip(offsets, offsets[1:])):
            raise Gen5MapFormatError(f"{magic} file offsets are not monotonic")
        files = tuple(raw[offsets[i]:offsets[i + 1]] for i in range(file_count))
        return cls(
            magic=magic,
            file_count=file_count,
            offsets=offsets,
            files=files,
            padded=bool(offsets and all(value % 0x80 == 0 for value in offsets)),
            raw_size=len(raw),
        )


@dataclass(frozen=True)
class ChunkBuilding:
    index: int
    local_x: float
    local_y: float
    local_z: float
    raw_x_fx32: int
    raw_y_fx32: int
    raw_z_fx32: int
    rotation_raw: int
    rotation_degrees: float
    model_uid: int

    @classmethod
    def parse_many(cls, raw: bytes) -> tuple["ChunkBuilding", ...]:
        _require(raw, 4, "ChunkBuildings")
        count = _u32(raw, 0)
        required = 4 + count * 16
        if count > 65535 or required > len(raw):
            raise Gen5MapFormatError(
                f"ChunkBuildings declares {count} records / {required} bytes, file has {len(raw)}"
            )
        result: list[ChunkBuilding] = []
        cursor = 4
        for index in range(count):
            x, y, z = struct.unpack_from("<iii", raw, cursor)
            rotation, model_uid = struct.unpack_from("<HH", raw, cursor + 12)
            result.append(cls(
                index=index,
                local_x=x / FX32_ONE,
                local_y=y / FX32_ONE,
                local_z=z / FX32_ONE,
                raw_x_fx32=x,
                raw_y_fx32=y,
                raw_z_fx32=z,
                rotation_raw=rotation,
                rotation_degrees=rotation * 360.0 / ANGLE16_FULL,
                model_uid=model_uid,
            ))
            cursor += 16
        return tuple(result)


@dataclass(frozen=True)
class MapChunk:
    chunk_id: int
    container_magic: str
    terrain_model: bytes
    auxiliary_files: tuple[bytes, ...]
    building_blob: bytes
    buildings: tuple[ChunkBuilding, ...]

    @classmethod
    def parse(cls, raw: bytes, chunk_id: int) -> "MapChunk":
        container = GFContainer.parse(raw)
        if not container.files:
            raise Gen5MapFormatError(f"MapChunk[{chunk_id}] has no files")
        terrain = container.files[0]
        building_blob = container.files[-1]
        try:
            buildings = ChunkBuilding.parse_many(building_blob)
        except Gen5MapFormatError:
            # Some special/no-grid chunks may not carry ChunkBuildings in the
            # normal last-file format.  Keep the raw file, but never invent a
            # building list.
            buildings = ()
        return cls(
            chunk_id=chunk_id,
            container_magic=container.magic,
            terrain_model=terrain,
            auxiliary_files=container.files[1:-1],
            building_blob=building_blob,
            buildings=buildings,
        )


@dataclass(frozen=True)
class AreaBuildingResource:
    resource_index: int
    uid: int
    type: int
    door_uid: int | None
    door_x: int
    door_y: int
    door_z: int
    unknown1: int
    animation_container_type: int | None
    unknown2: int | None
    animation_set_entry_count: int | None
    animation_count: int | None
    raw_metadata: bytes
    model: bytes

    @property
    def has_door_metadata(self) -> bool:
        return self.door_uid is not None

    @classmethod
    def parse(cls, metadata: bytes, model: bytes, resource_index: int) -> "AreaBuildingResource":
        _require(metadata, 16, f"AreaBuildingResource[{resource_index}]")
        uid = _u16(metadata, 0)
        type_id = _u16(metadata, 2)
        door_raw = _u16(metadata, 4)
        door_uid = None if door_raw == 0xFFFF else door_raw
        return cls(
            resource_index=resource_index,
            uid=uid,
            type=type_id,
            door_uid=door_uid,
            door_x=_s16(metadata, 6),
            door_y=_s16(metadata, 8),
            door_z=_s16(metadata, 10),
            unknown1=_s16(metadata, 12),
            animation_container_type=metadata[16] if len(metadata) > 16 else None,
            unknown2=metadata[17] if len(metadata) > 17 else None,
            animation_set_entry_count=metadata[18] if len(metadata) > 18 else None,
            animation_count=metadata[19] if len(metadata) > 19 else None,
            raw_metadata=metadata,
            model=model,
        )


@dataclass(frozen=True)
class AreaBuildingBundle:
    buildings_id: int
    is_exterior: bool
    container_magic: str
    resources: tuple[AreaBuildingResource, ...]

    @classmethod
    def parse(cls, raw: bytes, buildings_id: int, is_exterior: bool) -> "AreaBuildingBundle":
        container = GFContainer.parse(raw)
        if container.file_count % 2:
            raise Gen5MapFormatError(
                f"AreaBuildingBundle[{buildings_id}] file_count {container.file_count} is not even"
            )
        count = container.file_count // 2
        resources = tuple(
            AreaBuildingResource.parse(
                container.files[index], container.files[count + index], index
            )
            for index in range(count)
        )
        return cls(
            buildings_id=buildings_id,
            is_exterior=is_exterior,
            container_magic=container.magic,
            resources=resources,
        )

    def by_uid(self, uid: int) -> AreaBuildingResource | None:
        return next((item for item in self.resources if item.uid == uid), None)


# Existing project event decoder, but keyed by the *correct* ZoneHeader.entitiesID.
EVENT_HEADER_SIZE = 8
FURNITURE_SIZE = 0x14
NPC_SIZE = 0x24
WARP_SIZE = 0x14
TRIGGER_SIZE = 0x16


def decode_entities(raw: bytes, entities_id: int) -> dict[str, Any]:
    _require(raw, EVENT_HEADER_SIZE, f"Entities[{entities_id}]")
    declared_length = _u32(raw, 0)
    furniture_count, npc_count, warp_count, trigger_count = raw[4:8]
    required = (
        EVENT_HEADER_SIZE
        + furniture_count * FURNITURE_SIZE
        + npc_count * NPC_SIZE
        + warp_count * WARP_SIZE
        + trigger_count * TRIGGER_SIZE
    )
    if required > len(raw):
        raise Gen5MapFormatError(
            f"Entities[{entities_id}] declares {required} bytes but contains {len(raw)}"
        )
    cursor = EVENT_HEADER_SIZE
    furniture: list[dict[str, Any]] = []
    for index in range(furniture_count):
        rec = raw[cursor:cursor + FURNITURE_SIZE]
        cursor += FURNITURE_SIZE
        furniture.append({
            "id": index,
            "script_id": _u16(rec, 0),
            "x": _s32(rec, 8),
            "y": _s32(rec, 12),
            "z": _s32(rec, 16),
            "coordinate_units": "map_local_tiles_candidate",
        })
    npcs: list[dict[str, Any]] = []
    for index in range(npc_count):
        rec = raw[cursor:cursor + NPC_SIZE]
        cursor += NPC_SIZE
        npcs.append({
            "record_index": index,
            "id": _u16(rec, 0),
            "sprite_id": _u16(rec, 2),
            "movement_id": _u16(rec, 4),
            "flag_id": _u16(rec, 8),
            "script_id": _u16(rec, 10),
            "facing_id": _u16(rec, 12),
            "x": _s16(rec, 28),
            "y": _s16(rec, 30),
            "z": _s16(rec, 34),
            "coordinate_units": "map_local_tiles_candidate",
        })
    warps: list[dict[str, Any]] = []
    for index in range(warp_count):
        rec = raw[cursor:cursor + WARP_SIZE]
        cursor += WARP_SIZE
        x_world, y_world = _s16(rec, 8), _s16(rec, 12)
        warps.append({
            "id": index,
            "target_zone_or_map_raw": _u16(rec, 0),
            "target_warp_id": _u16(rec, 2),
            "kind": _u16(rec, 4),
            "x_world": x_world,
            "y_world": y_world,
            "z": _s16(rec, 18),
            "tile_x_candidate": x_world / 16.0,
            "tile_y_candidate": y_world / 16.0,
            "width": max(1, _u16(rec, 14)),
            "height": max(1, _u16(rec, 16)),
            "coordinate_units": "map_world_units_16_per_tile_candidate",
            "destination_semantics": "raw; promote only after live transition evidence",
        })
    triggers: list[dict[str, Any]] = []
    for index in range(trigger_count):
        rec = raw[cursor:cursor + TRIGGER_SIZE]
        cursor += TRIGGER_SIZE
        triggers.append({
            "id": index,
            "entity_id": _u16(rec, 0),
            "constant": _u16(rec, 2),
            "reference": _u16(rec, 4),
            "x": _s16(rec, 10),
            "y": _s16(rec, 12),
            "z": _s16(rec, 14),
            "coordinate_units": "map_local_tiles_candidate",
        })
    return {
        "entities_id": entities_id,
        "declared_length": declared_length,
        "counts": {
            "furniture": furniture_count,
            "npcs": npc_count,
            "warps": warp_count,
            "triggers": trigger_count,
        },
        "furniture": furniture,
        "npcs": npcs,
        "warps": warps,
        "triggers": triggers,
        "source": f"rom:/{ZONE_ENTITIES_PATH}[{entities_id}]",
    }


@dataclass(frozen=True)
class PermissionModel:
    chunk_id: int
    width: int
    height: int
    planes: tuple[tuple[int, ...], ...]
    source_file_index: int | None


def decode_permission_from_chunk(chunk: MapChunk) -> PermissionModel | None:
    """Preserve raw permission planes without assigning passability semantics.

    Gen-5 map containers normally keep the permission blob in an auxiliary
    file.  We accept either 8 or 16 contiguous byte planes when its dimensions
    and length self-consistently prove the shape.
    """
    for file_index, raw in enumerate(chunk.auxiliary_files, start=1):
        if len(raw) < 4:
            continue
        width, height = struct.unpack_from("<HH", raw, 0)
        if not (1 <= width <= 512 and 1 <= height <= 512):
            continue
        cells = width * height
        remaining = len(raw) - 4
        if remaining not in (cells * 8, cells * 16):
            continue
        plane_count = remaining // cells
        planes = tuple(
            tuple(raw[4 + p * cells:4 + (p + 1) * cells])
            for p in range(plane_count)
        )
        return PermissionModel(chunk.chunk_id, width, height, planes, file_index)
    return None


class Gen5RomMap:
    """Lazy read-only static map database for one B2/W2 ROM."""

    def __init__(self, rom_path: str | Path | None = None) -> None:
        selected = str(rom_path) if rom_path else os.getenv("BLACK2_ROM_PATH")
        if not selected:
            raise FileNotFoundError("BLACK2_ROM_PATH is not set and no ROM path was supplied")
        self.rom = NitroRom(selected)
        self.zone_data = self.rom.read_file(ZONE_DATA_PATH)
        self.area_data = self.rom.read_file(AREA_DATA_PATH)
        self._archive_cache: dict[str, NarcArchive] = {}
        self.zone_count_actual, self.zone_data_trailing = divmod(len(self.zone_data), ZONE_RECORD_SIZE)
        if self.zone_count_actual == 0:
            raise Gen5MapFormatError(
                f"ZoneData size {len(self.zone_data)} is too small for a 0x30 record"
            )
        self.area_count_actual, self.area_data_trailing = divmod(len(self.area_data), AREA_RECORD_SIZE)
        if self.area_count_actual == 0:
            raise Gen5MapFormatError(
                f"AreaData size {len(self.area_data)} is too small for an AREA record"
            )

    @property
    def zone_count(self) -> int:
        return self.zone_count_actual

    @property
    def area_count(self) -> int:
        return self.area_count_actual

    def archive(self, path: str) -> NarcArchive:
        if path not in self._archive_cache:
            self._archive_cache[path] = NarcArchive(self.rom.read_file(path))
        return self._archive_cache[path]

    @lru_cache(maxsize=2048)
    def zone(self, zone_id: int) -> ZoneHeader:
        if not 0 <= zone_id < self.zone_count:
            raise IndexError(f"Zone {zone_id} outside 0..{self.zone_count - 1}")
        start = zone_id * ZONE_RECORD_SIZE
        return ZoneHeader.parse(self.zone_data[start:start + ZONE_RECORD_SIZE], zone_id)

    @lru_cache(maxsize=512)
    def area(self, area_id: int) -> AreaHeader:
        if not 0 <= area_id < self.area_count:
            raise IndexError(f"Area {area_id} outside 0..{self.area_count - 1}")
        start = area_id * AREA_RECORD_SIZE
        return AreaHeader.parse(self.area_data[start:start + AREA_RECORD_SIZE], area_id)

    @lru_cache(maxsize=512)
    def matrix(self, matrix_id: int) -> MapMatrix:
        files = self.archive(MAP_MATRIX_PATH).files
        if not 0 <= matrix_id < len(files):
            raise IndexError(f"Matrix {matrix_id} outside {MAP_MATRIX_PATH}")
        return MapMatrix.parse(files[matrix_id], matrix_id)

    @lru_cache(maxsize=4096)
    def chunk(self, chunk_id: int) -> MapChunk:
        files = self.archive(MAP_CHUNKS_PATH).files
        if not 0 <= chunk_id < len(files):
            raise IndexError(f"Chunk {chunk_id} outside {MAP_CHUNKS_PATH}")
        return MapChunk.parse(files[chunk_id], chunk_id)

    @lru_cache(maxsize=2048)
    def entities(self, entities_id: int) -> dict[str, Any]:
        files = self.archive(ZONE_ENTITIES_PATH).files
        if not 0 <= entities_id < len(files):
            raise IndexError(f"Entities {entities_id} outside {ZONE_ENTITIES_PATH}")
        return decode_entities(files[entities_id], entities_id)

    @lru_cache(maxsize=512)
    def building_bundle(self, area_id: int) -> AreaBuildingBundle:
        area = self.area(area_id)
        path = BUILDING_BUNDLE_EXT_PATH if area.is_exterior else BUILDING_BUNDLE_INT_PATH
        files = self.archive(path).files
        if not 0 <= area.buildings_id < len(files):
            raise IndexError(
                f"Area {area_id} buildingsID {area.buildings_id} outside {path}"
            )
        return AreaBuildingBundle.parse(
            files[area.buildings_id], area.buildings_id, area.is_exterior
        )

    def terrain_texture(self, area_id: int) -> bytes:
        area = self.area(area_id)
        files = self.archive(MAP_TEXTURE_PATH).files
        if not 0 <= area.textures_id < len(files):
            raise IndexError(f"Area {area_id} texturesID {area.textures_id} outside {MAP_TEXTURE_PATH}")
        return files[area.textures_id]

    def building_texture(self, area_id: int) -> bytes:
        area = self.area(area_id)
        path = BUILDING_TEXTURE_EXT_PATH if area.is_exterior else BUILDING_TEXTURE_INT_PATH
        files = self.archive(path).files
        if not 0 <= area.buildings_id < len(files):
            raise IndexError(f"Area {area_id} buildingsID {area.buildings_id} outside {path}")
        return files[area.buildings_id]

    def static_identity(self) -> dict[str, Any]:
        return {
            "rom_path": str(self.rom.path),
            "rom_name": self.rom.path.name,
            "zone_count": self.zone_count,
            "area_count": self.area_count,
            "archives": {
                "chunks": MAP_CHUNKS_PATH,
                "matrices": MAP_MATRIX_PATH,
                "zones": ZONE_DATA_PATH,
                "areas": AREA_DATA_PATH,
                "terrain_textures": MAP_TEXTURE_PATH,
                "entities": ZONE_ENTITIES_PATH,
                "building_texture_ext": BUILDING_TEXTURE_EXT_PATH,
                "building_texture_int": BUILDING_TEXTURE_INT_PATH,
                "building_bundle_ext": BUILDING_BUNDLE_EXT_PATH,
                "building_bundle_int": BUILDING_BUNDLE_INT_PATH,
            },
            "policy": "static ROM facts only; current identity must come from runtime RAM",
        }


def dataclass_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value
