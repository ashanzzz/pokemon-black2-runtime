"""ROM-native BMD0/BTX0 map extraction for the v2 runtime."""
from __future__ import annotations

from collections import Counter
import asyncio
from dataclasses import dataclass
import hashlib
import json
import time
from pathlib import Path
import re
import struct
import subprocess
from typing import Any, Iterable

from ..memory.reader import MemoryReader
from ..decoders.field import get_map_name
from .rom_maps import NativeMapEngine
from .rom_reader import NarcArchive
from .runtime_player_state import player_runtime_service


MAP_MODEL_PATH = "a/0/0/8"
MAP_TEXTURE_PATH = "a/0/1/4"
MAP_ID_OFFSETS = (0x1434A6, 0x143668, 0x146CE8, 0x146EB2)
_MATRIX_NONE = 0xFFFFFFFF
_NAME_RE = re.compile(rb"[A-Za-z][A-Za-z0-9_]{2,}")
_VISUAL_SCAN_LOCK = asyncio.Lock()

# This model has three exact material/palette candidates.  Archive 282 was
# selected by a direct BizHawk screen comparison; 307 showed a blue variant.
_SCREEN_VALIDATED_TEXTURE_IDS = {"m_h02_00_00": 282}


class NativeMapError(RuntimeError):
    """The current ROM-native map cannot be proven or rendered."""


@dataclass(frozen=True)
class LiveMapState:
    map_id: int | None
    x: int | None
    y: int | None
    elevation: int | None
    verified: bool
    facing: str = "Unresolved"
    movement_state: str = "Unresolved"


def _bytes_from_result(result: dict[str, Any]) -> bytes:
    values = result.get("bytes")
    if values is not None:
        return bytes(int(value) & 0xFF for value in values)
    return bytes.fromhex(str(result.get("hex", "")))


def _u16(data: bytes) -> int:
    return struct.unpack_from("<H", data)[0] if len(data) >= 2 else 0


async def read_live_map_state(reader: MemoryReader, *, force_sample: bool = False) -> LiveMapState:
    """Read the live player from the verified runtime Field object graph.

    Current facing is FieldActor.FaceDir, cross-checked by
    PlayerState.RotationAngle inside PlayerRuntimeService.  A failed player
    decode remains unresolved; it is never replaced by a default direction.
    """
    # The SemanticStateEngine is the canonical high-frequency sampler and calls
    # this function with force_sample=True. Other consumers (map HUD, schematic,
    # cache observer) reuse that sample so temporal gait measurements are not
    # distorted by unrelated map polling. Bootstrap once if no sample exists.
    sample = player_runtime_service.latest
    if force_sample or not sample:
        try:
            sample = await player_runtime_service.sample(reader)
        except Exception:
            sample = None
    if not sample or sample.get("status") not in {"resolved", "candidate"}:
        fallback_error: Exception | None = None
        for attempt in range(3):
            try:
                results = await reader.read_batch_ranges(_live_state_ranges())
                if results:
                    return _decode_live_map_state(results)
                fallback_error = NativeMapError("BizHawk bridge returned an empty map-state batch")
            except (ConnectionError, TimeoutError, OSError, RuntimeError) as error:
                fallback_error = error
            if attempt < 2:
                await asyncio.sleep(0.05)
        return LiveMapState(
            map_id=None, x=None, y=None, elevation=None, verified=False,
            facing="Unresolved", movement_state="Unresolved",
        )
    pos = (sample.get("position") or {}).get("grid") or {}
    orient = sample.get("orientation") or {}
    locomotion = sample.get("locomotion") or {}
    verified = bool(
        sample.get("status") == "resolved"
        and orient.get("verified")
        and isinstance(pos.get("x"), int)
        and isinstance(pos.get("z"), int)
    )
    return LiveMapState(
        map_id=None,
        x=pos.get("x") if verified else None,
        y=pos.get("z") if verified else None,
        elevation=pos.get("y") if verified else None,
        verified=verified,
        facing=orient.get("facing", "Unresolved") if verified else "Unresolved",
        movement_state=locomotion.get("semantic_state", "Unresolved") if verified else "Unresolved",
    )

def _live_state_ranges() -> list[dict[str, int | str]]:
    return [
        {"id": f"map_{index}", "addr": offset, "length": 2}
        for index, offset in enumerate(MAP_ID_OFFSETS)
    ]


def _decode_live_map_state(results: dict[str, Any]) -> LiveMapState:
    map_values = [
        _u16(_bytes_from_result(results.get(f"map_{index}", {})))
        for index in range(len(MAP_ID_OFFSETS))
    ]
    plausible_maps = [value for value in map_values if 0 < value < 4096]
    map_vote = Counter(plausible_maps).most_common(1)
    map_id = map_vote[0][0] if map_vote and map_vote[0][1] >= 2 else None

    # The former 0x0223DE00 candidate and three 0x02143620 mirrors were
    # rejected by the controlled EXP_015 input sequence.  They remain absent
    # here until a GameSystem -> Field -> FieldPlayer -> Core -> PlayerActor
    # chain is resolved and lifecycle-validated for this ROM/session.
    return LiveMapState(
        map_id=map_id,
        x=None,
        y=None,
        elevation=None,
        verified=False,
        facing="Unresolved",
        movement_state="Unresolved",
    )


def _embedded_bmd0(payload: bytes, model_id: int) -> bytes:
    if len(payload) < 16:
        raise NativeMapError(f"model {model_id} is shorter than its header")
    _signature, start, end, _total = struct.unpack_from("<4I", payload)
    if not (16 <= start < end <= len(payload)) or payload[start:start + 4] != b"BMD0":
        raise NativeMapError(f"model {model_id} has invalid BMD0 boundaries")
    return payload[start:end]


def _model_name(bmd0: bytes) -> str:
    names = sorted(name for name in _NAME_RE.findall(bmd0) if name.startswith(b"m_"))
    return names[0].decode("ascii") if names else "unnamed_model"


def _material_texture_names(bmd0: bytes) -> tuple[bytes, ...]:
    """Return only BMD0 texture names paired with their palette names."""
    names = set(_NAME_RE.findall(bmd0))
    bases = sorted(
        name for name in names if not name.endswith(b"_pl") and name + b"_pl" in names
    )
    if not bases:
        raise NativeMapError("BMD0 contains no texture/palette name pairs")
    return tuple(component for base in bases for component in (base, base + b"_pl"))


def _texture_candidate_ids(
    material_names: tuple[bytes, ...], texture_files: tuple[bytes, ...]
) -> tuple[int, ...]:
    return tuple(
        texture_id
        for texture_id, texture in enumerate(texture_files)
        if texture[:4] == b"BTX0" and all(name in texture for name in material_names)
    )


def _select_texture_for_model(
    model_id: int,
    model_files: tuple[bytes, ...],
    texture_files: tuple[bytes, ...],
    preferred_id: int | None,
) -> tuple[int, int, int]:
    """Select a BTX0 only after every BMD0 material/palette pair matches."""
    bmd0 = _embedded_bmd0(model_files[model_id], model_id)
    expected = _material_texture_names(bmd0)
    candidates = _texture_candidate_ids(expected, texture_files)
    if not candidates:
        raise NativeMapError(f"model {model_id} has no exact ROM BTX0 material match")
    if preferred_id in candidates:
        return preferred_id, len(expected), len(expected)
    if len(candidates) == 1:
        return candidates[0], len(expected), len(expected)
    calibrated_id = _SCREEN_VALIDATED_TEXTURE_IDS.get(_model_name(bmd0))
    if calibrated_id in candidates:
        return calibrated_id, len(expected), len(expected)
    if candidates:
        return candidates[0], len(expected), len(expected)
    raise NativeMapError(
        f"model {model_id} has multiple exact BTX0 candidates {list(candidates)} without screen calibration"
    )


def _texture_candidates_for_model(
    model_id: int,
    model_files: tuple[bytes, ...],
    texture_files: tuple[bytes, ...],
) -> tuple[int, ...]:
    """Return all BTX0 archives that exactly contain a model's materials."""
    bmd0 = _embedded_bmd0(model_files[model_id], model_id)
    return _texture_candidate_ids(_material_texture_names(bmd0), texture_files)


def _prefix_map(files: tuple[bytes, ...], embedded: bool) -> dict[bytes, int]:
    result: dict[bytes, int] = {}
    for index, payload in enumerate(files):
        try:
            value = _embedded_bmd0(payload, index) if embedded else payload
        except NativeMapError:
            continue
        if value[:4] not in {b"BMD0", b"BTX0"}:
            continue
        prefix = value[:16]
        if prefix in result:
            result[prefix] = -1
        else:
            result[prefix] = index
    return {prefix: index for prefix, index in result.items() if index >= 0}


def _match_headers(matches: Iterable[dict[str, Any]], kind: str, prefixes: dict[bytes, int]) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for match in matches:
        if match.get("kind") != kind:
            continue
        try:
            asset_id = prefixes.get(bytes.fromhex(str(match["prefix_hex"])))
            offset = int(match["offset"])
        except (KeyError, TypeError, ValueError):
            continue
        if asset_id is not None:
            result.setdefault(asset_id, []).append(offset)
    return result


def _matrix_tables(raw: bytes):
    if len(raw) < 8:
        raise NativeMapError("map matrix is truncated")
    width, height = struct.unpack_from("<HH", raw, 4)
    count = width * height
    if not width or not height or len(raw) < 8 + count * 4:
        raise NativeMapError("map matrix dimensions are invalid")
    models = struct.unpack_from(f"<{count}I", raw, 8)
    definitions = (
        struct.unpack_from(f"<{count}I", raw, 8 + count * 4)
        if len(raw) >= 8 + count * 8
        else None
    )
    return width, height, models, definitions


def _candidate_gallery(
    candidates: list[tuple[int, int, int, int, tuple[int, ...], tuple[int, ...] | None]],
    loaded_models: dict[int, list[int]],
    loaded_textures: dict[int, list[int]],
) -> dict[str, Any]:
    """Expose resident ROM models without inventing a player-to-matrix origin."""
    active_by_model: dict[int, dict[str, Any]] = {}
    scenes = []
    for _score, matrix_id, width, height, model_ids, definitions in candidates:
        cells = [
            {
                "x": index % width,
                "y": index // width,
                "model_id": model_id,
                "candidate_matrix_id": matrix_id,
                "scene_index": len(scenes),
            }
            for index, model_id in enumerate(model_ids)
            if model_id in loaded_models
        ]
        if not cells:
            continue
        for cell in cells:
            existing = active_by_model.get(cell["model_id"])
            if existing is None:
                cell["candidate_matrix_ids"] = [matrix_id]
                active_by_model[cell["model_id"]] = cell
            else:
                existing["candidate_matrix_ids"].append(matrix_id)
        scenes.append(
            {
                "matrix_id": matrix_id,
                "matrix_size": {"width": width, "height": height},
                "has_definition_table": definitions is not None,
                "resident_cells": cells,
            }
        )
    active = list(active_by_model.values())
    if not active:
        raise NativeMapError("no ROM matrix cell contains a loaded BMD0 model")
    return {
        "display_mode": "candidate-gallery",
        "matrix_id": None,
        "equivalent_matrix_ids": [],
        "matrix_size": None,
        "loaded_model_ids": sorted(loaded_models),
        "map_definition_id": None,
        "active_cells": active,
        "candidate_scenes": scenes,
        "map_definition_bounds": None,
        "texture_id": next(iter(loaded_textures)),
        "player_chunk": None,
        "player_local": None,
        "player_model_id": None,
        "chunk_tile_size": None,
        "verified": True,
        "player_alignment": {
            "verified": False,
            "reason": "ARM9 player coordinates have no verified origin in the resident map matrices.",
        },
        "verification": {
            "method": "BizHawk ARM9 BMD0/BTX0 headers matched to ROM; matrix candidates are shown separately because player-to-matrix alignment is unproven.",
            "loaded_bmd0_offsets": {str(key): value for key, value in sorted(loaded_models.items())},
            "loaded_btx0_offsets": {str(key): value for key, value in sorted(loaded_textures.items())},
        },
    }


async def inspect_loaded_visual_map(reader: MemoryReader, player_x: int, player_y: int, engine: NativeMapEngine) -> dict[str, Any]:
    """Serialize full-RAM header scans for the single-threaded Lua bridge."""
    async with _VISUAL_SCAN_LOCK:
        return await _inspect_loaded_visual_map(reader, player_x, player_y, engine)


async def _inspect_loaded_visual_map(reader: MemoryReader, player_x: int, player_y: int, engine: NativeMapEngine) -> dict[str, Any]:
    model_files = NarcArchive(engine.rom.read_file(MAP_MODEL_PATH)).files
    texture_files = NarcArchive(engine.rom.read_file(MAP_TEXTURE_PATH)).files
    model_prefixes = _prefix_map(model_files, embedded=True)
    texture_prefixes = _prefix_map(texture_files, embedded=False)
    try:
        matches = await reader.scan_headers()
    except (ConnectionError, TimeoutError, OSError, RuntimeError) as error:
        raise NativeMapError(
            "BizHawk bridge does not expose memory.scan_headers; reload the v2 Lua bridge"
        ) from error
    loaded_models = _match_headers(matches, "BMD0", model_prefixes)
    loaded_textures = _match_headers(matches, "BTX0", texture_prefixes)
    if not loaded_models:
        raise NativeMapError("no ROM BMD0 map model is currently loaded in ARM9 RAM")
    if len(loaded_textures) != 1:
        raise NativeMapError(f"expected one loaded map BTX0 archive, found {sorted(loaded_textures)}")

    candidates = []
    for matrix_id, raw in enumerate(engine.matrix_narc.files):
        try:
            width, height, model_ids, definitions = _matrix_tables(raw)
        except NativeMapError:
            continue
        score = len((set(model_ids) - {_MATRIX_NONE}) & set(loaded_models))
        if score:
            candidates.append((score, matrix_id, width, height, model_ids, definitions))
    if not candidates:
        raise NativeMapError("loaded BMD0 models do not occur in a ROM map matrix")
    highest = max(item[0] for item in candidates)
    highest_candidates = [item for item in candidates if item[0] == highest]
    selected = []
    for score, matrix_id, width, height, model_ids, definitions in highest_candidates:
        sample_id = next((model_id for model_id in model_ids if model_id in engine.models), None)
        if sample_id is None:
            continue
        model = engine.models[sample_id]
        chunk_x, local_x = divmod(player_x, model.width)
        chunk_y, local_y = divmod(player_y, model.height)

        # Check candidate chunk and boundary border chunks (e.g. y=704 or y=771 on chunk boundaries)
        candidate_chunks = [(chunk_x, chunk_y)]
        if chunk_y > 0:
            candidate_chunks.append((chunk_x, chunk_y - 1))
        if chunk_x > 0:
            candidate_chunks.append((chunk_x - 1, chunk_y))
        if chunk_y + 1 < height:
            candidate_chunks.append((chunk_x, chunk_y + 1))
        if chunk_x + 1 < width:
            candidate_chunks.append((chunk_x + 1, chunk_y))

        for cx, cy in candidate_chunks:
            if cx < width and cy < height:
                player_model = model_ids[cy * width + cx]
                if player_model in loaded_models and player_model in engine.models:
                    lx = player_x - cx * model.width
                    ly = player_y - cy * model.height
                    selected.append((matrix_id, width, height, model_ids, definitions, cx, cy, player_model, lx, ly))
                    break

    if not selected:
        return _candidate_gallery(highest_candidates, loaded_models, loaded_textures)
    matrix_id, width, height, model_ids, definitions, chunk_x, chunk_y, player_model, local_x, local_y = selected[0]
    player_dimensions = engine.models[player_model]
    local_x = player_x % player_dimensions.width
    local_y = player_y % player_dimensions.height
    definition_id = definitions[chunk_y * width + chunk_x] if definitions is not None else None
    if definition_id == _MATRIX_NONE:
        raise NativeMapError("player chunk has no map-definition ID")

    active = []
    if definitions is None:
        # A standalone interior matrix is one complete map. Its missing second
        # table is not evidence that only the current cell belongs to it.
        for index, model_id in enumerate(model_ids):
            if model_id == _MATRIX_NONE:
                continue
            if model_id not in engine.models:
                raise NativeMapError("standalone interior references an unsupported model")
            active.append({"x": index % width, "y": index // width, "model_id": model_id})
    else:
        pending = [(chunk_x, chunk_y)]
        seen = set()
        while pending:
            cell_x, cell_y = pending.pop()
            if (cell_x, cell_y) in seen or not (0 <= cell_x < width and 0 <= cell_y < height):
                continue
            seen.add((cell_x, cell_y))
            index = cell_y * width + cell_x
            if definitions[index] != definition_id:
                continue
            model_id = model_ids[index]
            if model_id == _MATRIX_NONE or model_id not in engine.models:
                raise NativeMapError("current map definition references an unsupported model")
            active.append({"x": cell_x, "y": cell_y, "model_id": model_id})
            pending.extend(((cell_x - 1, cell_y), (cell_x + 1, cell_y), (cell_x, cell_y - 1), (cell_x, cell_y + 1)))

    active.sort(key=lambda cell: (cell["y"], cell["x"]))
    min_x, max_x = min(cell["x"] for cell in active), max(cell["x"] for cell in active)
    min_y, max_y = min(cell["y"] for cell in active), max(cell["y"] for cell in active)
    return {
        "display_mode": "aligned-map",
        "matrix_id": matrix_id,
        "equivalent_matrix_ids": [item[0] for item in selected],
        "matrix_size": {"width": width, "height": height},
        "loaded_model_ids": sorted(loaded_models),
        "map_definition_id": definition_id,
        "active_cells": active,
        # Map events are attached by MapSchematicService after it has resolved
        # the current Map Header.  Do not put named or positioned guesses here.
        "entities": {"npcs": [], "furniture": [], "warps": [], "triggers": []},
        "map_definition_bounds": {
            "min_chunk_x": min_x, "max_chunk_x": max_x, "min_chunk_y": min_y, "max_chunk_y": max_y,
            "width": max_x - min_x + 1, "height": max_y - min_y + 1, "cell_count": len(active),
            "is_rectangular": len(active) == (max_x - min_x + 1) * (max_y - min_y + 1),
        },
        "texture_id": next(iter(loaded_textures)),
        "player_chunk": {"x": chunk_x, "y": chunk_y},
        "player_local": {"x": local_x, "y": local_y},
        "player_model_id": player_model,
        "chunk_tile_size": {"width": engine.models[player_model].width, "height": engine.models[player_model].height},
        "verified": True,
        "player_alignment": {
            "verified": True,
            "method": "Player coordinates selected a resident ROM matrix cell.",
        },
        "player_surface_projection": {
            "verified": False,
            "reason": (
                "Matrix chunk selection is verified, but no ROM transform from logical player tiles "
                "to this BMD0 model's X/Z surface has been decoded."
            ),
        },
        "verification": {
            "method": "BizHawk ARM9 BMD0/BTX0 headers matched to ROM, then player coordinates matched to a matrix cell.",
            "loaded_bmd0_offsets": {str(key): value for key, value in sorted(loaded_models.items())},
            "loaded_btx0_offsets": {str(key): value for key, value in sorted(loaded_textures.items())},
        },
    }


class NativeMapService:
    """Build and cache native GLBs for the current loaded map window."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
        self.engine = NativeMapEngine.get_instance()
        self.cache = root / "runtime" / "native_map_cache" / "live"
        self.region_cache = root / "runtime" / "native_map_cache" / "regions"
        self.geometry_cache = root / "runtime" / "native_map_cache" / "geometry"
        self.apicula = root / "runtime" / "tools" / "apicula" / "apicula.exe"
        self._cache_lock = asyncio.Lock()
        self._cache_state: dict[str, Any] = {"state": "idle", "cache_key": None}

    async def build_live(self, reader: MemoryReader) -> dict[str, Any]:
        engine = self.engine
        live = await read_live_map_state(reader)
        if live.x is None or live.y is None:
            raise NativeMapError("current ARM9 player coordinates are not available")
        visual = await inspect_loaded_visual_map(reader, live.x, live.y, engine)
        cache_key = self._cache_key(visual, live.map_id)
        scene = self._scene_descriptor(visual, live, cache_key)
        async with self._cache_lock:
            manifest, region_dir, cache_hit = await self._ensure_region_cache(
                visual, engine, cache_key,
            )
        rendered = self._rendered_models(visual, manifest, region_dir, cache_key)
        self._cache_state = {
            "state": "ready",
            "cache_key": cache_key,
            "hit": cache_hit,
            "map_section_id": live.map_id,
            "display_mode": visual.get("display_mode"),
            "scene": scene,
            "updated_at": time.time(),
        }
        return {
            **visual,
            "live_player": {
                "x": live.x,
                "y": live.y,
                "elevation": live.elevation,
                "facing": live.facing,
                "movement_state": live.movement_state,
                "map_section_id": live.map_id,
                "map_name": get_map_name(live.map_id) if live.map_id is not None else None,
                "verified": live.verified,
            },
            "texture_id": manifest["texture_id"],
            "loaded_texture_id": manifest["loaded_texture_id"],
            "texture_source": manifest["texture_source"],
            "models": rendered,
            "renderable": True,
            "cache": {
                "key": cache_key,
                "hit": cache_hit,
                "state": "ready",
            },
            "scene": scene,
            "renderer": "BizHawk ARM9-verified BMD0 + ROM material-matched BTX0 converted locally to glTF 2.0 binary",
            "map": {"map_section_id": live.map_id, "name": "当前 ARM9 已加载地图窗口", "canonical_name": "Current loaded map window"},
            "overworld_events": {"visible": {"warps": [], "furniture": [], "npcs": [], "triggers": []}, "source": "not migrated; no objects fabricated"},
        }

    async def build_geometry_live(self, reader: MemoryReader) -> dict[str, Any]:
        """Return BMD0 geometry assets that cannot request BTX0 or PNG textures."""
        live = await read_live_map_state(reader)
        if live.x is None or live.y is None:
            raise NativeMapError("current ARM9 player coordinates are not available")
        visual = await inspect_loaded_visual_map(reader, live.x, live.y, self.engine)
        cache_key = self._geometry_cache_key(visual)
        scene_key = self._cache_key(visual, live.map_id)
        scene = self._scene_descriptor(visual, live, scene_key)
        async with self._cache_lock:
            manifest, geometry_dir, cache_hit = await self._ensure_geometry_cache(
                visual, self.engine, cache_key,
            )
        return {
            **visual,
            "live_player": self._live_player(live),
            "models": self._rendered_geometry_models(visual, manifest, geometry_dir, cache_key),
            "renderable": True,
            "cache": {"key": cache_key, "hit": cache_hit, "state": "ready"},
            "scene": scene,
            "geometry": {
                "kind": "BMD0 geometry only",
                "texture_policy": "no BTX0, PNG, or official material image is returned",
                "code_policy": "M<number>.T<number> is a raw material slot, not a terrain label",
            },
            "renderer": "ROM BMD0 converted locally without BTX0 texture references",
        }

    async def start_auto_cache(
        self,
        reader: MemoryReader,
        interval: float = 0.75,
        stability_scan_interval: float = 20.0,
    ) -> None:
        """Cache a new loaded scene without repeatedly monopolizing the Lua bridge.

        Reading player state is small and safe to poll.  A BMD0/BTX0 scan reads
        all ARM9 main RAM, so it runs for the first scene, after a confirmed
        map/chunk transition, and as a slow stationary-scene safety check.
        Foreground map requests share the same serialized scan lock.
        """
        last_key: str | None = None
        last_scene: dict[str, Any] | None = None
        last_signature: tuple[int | None, int, int] | None = None
        last_scan_at = 0.0
        chunk_width = 32
        chunk_height = 32
        while True:
            try:
                if not reader.client.is_connected:
                    self._cache_state = {
                        "state": "waiting_for_bridge", "cache_key": last_key, "scene": last_scene,
                    }
                else:
                    live = await read_live_map_state(reader)
                    if live.x is None or live.y is None:
                        self._cache_state = {
                            "state": "waiting_for_map", "cache_key": last_key, "scene": last_scene,
                        }
                    else:
                        signature = (
                            live.map_id,
                            live.x // chunk_width,
                            live.y // chunk_height,
                        )
                        should_scan = (
                            last_key is None
                            or signature != last_signature
                            or time.monotonic() - last_scan_at >= stability_scan_interval
                        )
                        if not should_scan:
                            if last_scene is not None:
                                last_scene = {
                                    **last_scene,
                                    "player": {
                                        "x": live.x,
                                        "y": live.y,
                                        "elevation": live.elevation,
                                        "verified": live.verified,
                                    },
                                }
                            self._cache_state = {
                                **self._cache_state,
                                "state": "ready",
                                "cache_key": last_key,
                                "map_section_id": live.map_id,
                                "scene": last_scene,
                                "updated_at": time.time(),
                            }
                            await asyncio.sleep(interval)
                            continue
                        visual = await inspect_loaded_visual_map(reader, live.x, live.y, self.engine)
                        cache_key = self._cache_key(visual, live.map_id)
                        scene = self._scene_descriptor(visual, live, cache_key)
                        async with self._cache_lock:
                            _, _, cache_hit = await self._ensure_region_cache(
                                visual, self.engine, cache_key,
                        )
                        last_key = cache_key
                        last_scene = scene
                        tile_size = visual.get("chunk_tile_size") or {}
                        chunk_width = max(1, int(tile_size.get("width") or 32))
                        chunk_height = max(1, int(tile_size.get("height") or 32))
                        last_signature = (
                            live.map_id,
                            live.x // chunk_width,
                            live.y // chunk_height,
                        )
                        last_scan_at = time.monotonic()
                        self._cache_state = {
                            "state": "ready",
                            "cache_key": cache_key,
                            "hit": cache_hit,
                            "map_section_id": live.map_id,
                            "scene": scene,
                            "updated_at": time.time(),
                        }
            except (NativeMapError, ConnectionError, TimeoutError, OSError, ValueError) as error:
                self._cache_state = {
                    "state": "waiting_for_valid_scene",
                    "cache_key": last_key,
                    "scene": last_scene,
                    "error": str(error),
                    "updated_at": time.time(),
                }
            await asyncio.sleep(interval)

    def cache_status(self) -> dict[str, Any]:
        return {
            **self._cache_state,
            "region_cache": str(self.region_cache),
        }

    @staticmethod
    def _scene_descriptor(
        visual: dict[str, Any], live: LiveMapState, cache_key: str,
    ) -> dict[str, Any]:
        """Expose only the evidence used to auto-switch the map viewer."""
        return {
            "id": cache_key,
            "player": {
                "x": live.x,
                "y": live.y,
                "elevation": live.elevation,
                "verified": live.verified,
            },
            "alignment_verified": bool(visual.get("player_alignment", {}).get("verified")),
            "matrix_id": visual.get("matrix_id"),
            "map_definition_id": visual.get("map_definition_id"),
            "active_model_ids": sorted({
                int(cell["model_id"])
                for cell in visual.get("active_cells", [])
                if cell.get("model_id") is not None
            }),
            "loaded_model_ids": visual.get("loaded_model_ids", []),
            "active_cells": visual.get("active_cells", []),
            "texture_id": visual.get("texture_id"),
            "memory_offsets": visual.get("verification", {}).get("loaded_bmd0_offsets", {}),
        }

    def asset_path(self, cache_key: str, asset_key: str, asset_name: str) -> Path | None:
        if Path(asset_name).name != asset_name or Path(asset_name).suffix.lower() not in {".glb", ".png"}:
            return None
        if not re.fullmatch(r"[a-z0-9_-]{8,80}", cache_key):
            return None
        if not re.fullmatch(r"\d+(?:_tex_\d+)?", asset_key):
            return None
        path = self.region_cache / cache_key / "render" / f"model_{asset_key}" / asset_name
        return path if path.is_file() else None

    def geometry_asset_path(self, cache_key: str, asset_key: str, asset_name: str) -> Path | None:
        """Resolve only a texture-free geometry asset from the isolated cache."""
        if Path(asset_name).name != asset_name or Path(asset_name).suffix.lower() != ".glb":
            return None
        if not re.fullmatch(r"geometry_[a-z0-9]{20}", cache_key):
            return None
        if not re.fullmatch(r"\d+", asset_key):
            return None
        path = self.geometry_cache / cache_key / "render" / f"model_{asset_key}" / asset_name
        return path if path.is_file() else None

    def live_asset_path(self, asset_key: str, asset_name: str) -> Path | None:
        if Path(asset_name).name != asset_name or Path(asset_name).suffix.lower() not in {".glb", ".png"}:
            return None
        if not re.fullmatch(r"\d+(?:_tex_\d+)?", asset_key):
            return None
        path = self.cache / "render" / f"model_{asset_key}" / asset_name
        return path if path.is_file() else None

    def _cache_key(self, visual: dict[str, Any], map_section_id: int | None) -> str:
        identity = {
            "map_section_id": map_section_id,
            "display_mode": visual.get("display_mode"),
            "matrix_id": visual.get("matrix_id"),
            "equivalent_matrix_ids": visual.get("equivalent_matrix_ids", []),
            "map_definition_id": visual.get("map_definition_id"),
            "active_cells": visual.get("active_cells", []),
            "texture_id": visual.get("texture_id"),
            "texture_candidate_ids": visual.get("texture_candidate_ids", []),
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:20]
        prefix = "map" if visual.get("map_definition_id") is not None else "scene"
        return f"{prefix}_{digest}"

    @staticmethod
    def _geometry_cache_key(visual: dict[str, Any]) -> str:
        """Identify geometry without allowing texture candidates to change it."""
        identity = {
            "display_mode": visual.get("display_mode"),
            "matrix_id": visual.get("matrix_id"),
            "equivalent_matrix_ids": visual.get("equivalent_matrix_ids", []),
            "map_definition_id": visual.get("map_definition_id"),
            "active_cells": visual.get("active_cells", []),
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:20]
        return f"geometry_{digest}"

    async def _ensure_region_cache(
        self, visual: dict[str, Any], engine: NativeMapEngine, cache_key: str,
    ) -> tuple[dict[str, Any], Path, bool]:
        region_dir = self.region_cache / cache_key
        cached = self._read_complete_manifest(region_dir, cache_key)
        if cached is not None:
            return cached, region_dir, True

        source_dir = region_dir / "source"
        manifest = self._export_sources(visual, source_dir, engine)
        for source_item in manifest["models"]:
            output_dir = region_dir / "render" / f"model_{source_item['asset_key']}"
            glb = await self._ensure_glb(
                source_dir / source_item["bmd0"],
                source_dir / source_item["btx0"],
                output_dir,
                source_item["model_id"],
            )
            source_item["glb"] = glb.name
        manifest.update({
            "cache_key": cache_key,
            "cache_format": "black2-native-map-region/v1",
            "cached_at": time.time(),
        })
        region_dir.mkdir(parents=True, exist_ok=True)
        (region_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (region_dir / "complete.json").write_text(
            json.dumps({"format": "black2-native-map-region-complete/v1", "cache_key": cache_key}),
            encoding="utf-8",
        )
        self._write_cache_index(manifest)
        return manifest, region_dir, False

    def _read_complete_manifest(self, region_dir: Path, cache_key: str) -> dict[str, Any] | None:
        manifest_path = region_dir / "manifest.json"
        complete_path = region_dir / "complete.json"
        try:
            marker = json.loads(complete_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if marker.get("cache_key") != cache_key or manifest.get("cache_key") != cache_key:
                return None
            for item in manifest.get("models", []):
                glb_name = item.get("glb")
                output_dir = region_dir / "render" / f"model_{item['asset_key']}"
                if not glb_name or not self._glb_has_textures(output_dir / glb_name, output_dir):
                    return None
            return manifest
        except (OSError, KeyError, TypeError, ValueError, UnicodeDecodeError):
            return None

    async def _ensure_geometry_cache(
        self, visual: dict[str, Any], engine: NativeMapEngine, cache_key: str,
    ) -> tuple[dict[str, Any], Path, bool]:
        geometry_dir = self.geometry_cache / cache_key
        cached = self._read_complete_geometry_manifest(geometry_dir, cache_key)
        if cached is not None:
            return cached, geometry_dir, True

        source_dir = geometry_dir / "source"
        render_dir = geometry_dir / "render"
        source_dir.mkdir(parents=True, exist_ok=True)
        models = []
        for model_id in sorted({int(cell["model_id"]) for cell in visual["active_cells"]}):
            bmd_name = f"model_{model_id}.bmd0"
            bmd_path = source_dir / bmd_name
            bmd_path.write_bytes(_embedded_bmd0(engine.model_narc.files[model_id], model_id))
            output_dir = render_dir / f"model_{model_id}"
            glb_path = await self._ensure_geometry_glb(bmd_path, output_dir, model_id)
            models.append({
                "model_id": model_id,
                "asset_key": str(model_id),
                "bmd0": bmd_name,
                "glb": glb_path.name,
                "material_codes": self._geometry_material_codes(bmd_path.read_bytes(), model_id),
            })
        manifest = {
            "cache_key": cache_key,
            "cache_format": "black2-geometry-only/v1",
            "geometry": "BMD0 mesh without BTX0 texture references",
            "models": models,
            "cached_at": time.time(),
        }
        geometry_dir.mkdir(parents=True, exist_ok=True)
        (geometry_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (geometry_dir / "complete.json").write_text(
            json.dumps({"format": "black2-geometry-only-complete/v1", "cache_key": cache_key}),
            encoding="utf-8",
        )
        return manifest, geometry_dir, False

    @staticmethod
    def _read_complete_geometry_manifest(
        geometry_dir: Path, cache_key: str,
    ) -> dict[str, Any] | None:
        try:
            marker = json.loads((geometry_dir / "complete.json").read_text(encoding="utf-8"))
            manifest = json.loads((geometry_dir / "manifest.json").read_text(encoding="utf-8"))
            if marker.get("cache_key") != cache_key or manifest.get("cache_key") != cache_key:
                return None
            for item in manifest.get("models", []):
                path = geometry_dir / "render" / f"model_{item['asset_key']}" / item["glb"]
                if not NativeMapService._geometry_glb_is_ready(path):
                    return None
            return manifest
        except (OSError, KeyError, TypeError, ValueError, UnicodeDecodeError):
            return None

    def _rendered_geometry_models(
        self, visual: dict[str, Any], manifest: dict[str, Any], geometry_dir: Path, cache_key: str,
    ) -> list[dict[str, Any]]:
        models = {int(item["model_id"]): item for item in manifest["models"]}
        rendered = []
        for cell in visual["active_cells"]:
            source = models.get(int(cell["model_id"]))
            if source is None:
                raise NativeMapError(f"geometry cache is missing model {cell['model_id']}")
            glb_path = geometry_dir / "render" / f"model_{source['asset_key']}" / source["glb"]
            if not self._geometry_glb_is_ready(glb_path):
                raise NativeMapError(f"geometry GLB is incomplete for model {cell['model_id']}")
            rendered.append({
                **source,
                "cell": dict(cell),
                "asset_url": (
                    f"/api/v1/map/geometry/cache/{cache_key}/"
                    f"{source['asset_key']}/{source['glb']}"
                ),
            })
        return rendered

    def _rendered_models(
        self, visual: dict[str, Any], manifest: dict[str, Any], region_dir: Path, cache_key: str,
    ) -> list[dict[str, Any]]:
        rendered = []
        for cell in visual["active_cells"]:
            source_items = [
                item for item in manifest["models"] if item["model_id"] == cell["model_id"]
            ]
            for source_item in source_items:
                asset_key = source_item.get("asset_key", str(cell["model_id"]))
                glb_name = source_item["glb"]
                glb_path = region_dir / "render" / f"model_{asset_key}" / glb_name
                if not glb_path.is_file():
                    raise NativeMapError(f"cached GLB is missing for model {cell['model_id']}")
                rendered.append({
                    **source_item,
                    "cell": {**cell, "texture_candidate": source_item.get("texture_candidate", False)},
                    "asset_url": f"/api/v1/map/visual/cache/{cache_key}/{asset_key}/{glb_name}",
                })
        return rendered

    def _write_cache_index(self, manifest: dict[str, Any]) -> None:
        self.region_cache.mkdir(parents=True, exist_ok=True)
        index_path = self.region_cache / "index.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"regions": []}
        except (OSError, ValueError, UnicodeDecodeError):
            index = {"regions": []}
        regions = [item for item in index.get("regions", []) if item.get("cache_key") != manifest["cache_key"]]
        regions.append({
            "cache_key": manifest["cache_key"],
            "map_definition_id": manifest.get("map_definition_id"),
            "matrix_id": manifest.get("matrix_id"),
            "display_mode": manifest.get("display_mode"),
            "cached_at": manifest.get("cached_at"),
        })
        index_path.write_text(json.dumps({"format": "black2-native-map-cache-index/v1", "regions": regions}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _export_sources(self, visual: dict[str, Any], destination: Path, engine: NativeMapEngine) -> dict[str, Any]:
        model_files = NarcArchive(engine.rom.read_file(MAP_MODEL_PATH)).files
        texture_files = NarcArchive(engine.rom.read_file(MAP_TEXTURE_PATH)).files
        loaded_texture_id = int(visual["texture_id"])
        model_ids = sorted({cell["model_id"] for cell in visual["active_cells"]})
        destination.mkdir(parents=True, exist_ok=True)
        models = []
        candidate_gallery = visual.get("display_mode") == "texture-candidate-gallery"
        resolved = {}
        for model_id in model_ids:
            if candidate_gallery:
                ambiguous_ids = visual.get("texture_ambiguous_models", {}).get(str(model_id))
                candidate_ids = tuple(ambiguous_ids or (
                    _select_texture_for_model(
                        model_id, model_files, texture_files, loaded_texture_id
                    )[0],
                ))
                bmd0 = _embedded_bmd0(model_files[model_id], model_id)
                expected_count = len(_material_texture_names(bmd0))
                resolved[model_id] = tuple(
                    (candidate_id, expected_count, expected_count)
                    for candidate_id in candidate_ids
                )
            else:
                resolved[model_id] = (_select_texture_for_model(
                    model_id, model_files, texture_files, loaded_texture_id
                ),)
            name = f"model_{model_id}.bmd0"
            (destination / name).write_bytes(_embedded_bmd0(model_files[model_id], model_id))
            for selected_id, match_count, expected_count in resolved[model_id]:
                (destination / f"texture_{selected_id}.btx0").write_bytes(texture_files[selected_id])
                asset_key = f"{model_id}_tex_{selected_id}" if candidate_gallery else str(model_id)
                models.append(
                    {
                        "model_id": model_id,
                        "asset_key": asset_key,
                        "bmd0": name,
                        "btx0": f"texture_{selected_id}.btx0",
                        "texture_id": selected_id,
                        "texture_match": f"{match_count}/{expected_count}",
                        "texture_candidate": candidate_gallery,
                    }
                )
        player_model_id = visual.get("player_model_id")
        selected_for_player = resolved.get(player_model_id, ())
        texture_id = selected_for_player[0][0] if len(selected_for_player) == 1 else None
        manifest = {
            **visual,
            "texture_id": texture_id,
            "btx0": f"texture_{texture_id}.btx0" if texture_id is not None else None,
            "loaded_texture_id": loaded_texture_id,
            "texture_source": (
                "ROM BMD0 material-name candidates; screen calibration pending"
                if candidate_gallery else "ROM BMD0 material-name match"
            ),
            "models": models,
            "format": "black2-live-map-window/v2",
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    async def _ensure_geometry_glb(self, model_path: Path, output_dir: Path, model_id: int) -> Path:
        """Convert BMD0 alone, then remove every texture reference from the GLB."""
        output_dir.mkdir(parents=True, exist_ok=True)
        existing = next((path for path in output_dir.glob("*.glb") if self._geometry_glb_is_ready(path)), None)
        if existing is not None:
            return existing
        if not self.apicula.is_file():
            raise NativeMapError("Apicula converter is missing from v2 runtime/tools/apicula")
        result = await asyncio.to_thread(
            subprocess.run,
            [
                str(self.apicula), "convert", "-f", "glb", "--overwrite",
                str(model_path), "-o", str(output_dir),
            ],
            cwd=self.geometry_cache.parents[2], capture_output=True, text=True, timeout=60, check=False,
        )
        generated = sorted(output_dir.glob("*.glb"))
        if result.returncode or len(generated) != 1:
            detail = (result.stderr or result.stdout).strip() or "converter produced no single GLB"
            raise NativeMapError(f"Nitro geometry conversion failed for model {model_id}: {detail}")
        self._strip_glb_texture_references(generated[0])
        if not self._geometry_glb_is_ready(generated[0]):
            raise NativeMapError(f"geometry GLB retained texture references for model {model_id}")
        return generated[0]

    @staticmethod
    def _strip_glb_texture_references(glb_path: Path) -> None:
        """Keep geometry buffers intact while making texture fetches impossible."""
        try:
            payload = glb_path.read_bytes()
            magic, version, total_length = struct.unpack_from("<4sII", payload, 0)
            json_length, json_type = struct.unpack_from("<I4s", payload, 12)
            if magic != b"glTF" or version != 2 or total_length != len(payload) or json_type != b"JSON":
                raise ValueError("invalid GLB header")
            document = json.loads(payload[20:20 + json_length].decode("utf-8").rstrip(" \t\r\n\x00"))
        except (OSError, ValueError, UnicodeDecodeError, struct.error) as error:
            raise NativeMapError("geometry converter produced an invalid GLB") from error

        for material in document.get("materials", []):
            pbr = material.get("pbrMetallicRoughness")
            if isinstance(pbr, dict):
                pbr.pop("baseColorTexture", None)
                pbr.pop("metallicRoughnessTexture", None)
                pbr.setdefault("baseColorFactor", [0.64, 0.64, 0.64, 1.0])
            material.pop("normalTexture", None)
            material.pop("occlusionTexture", None)
            material.pop("emissiveTexture", None)
        document.pop("images", None)
        document.pop("textures", None)
        document.pop("samplers", None)
        for extension_key in ("extensionsUsed", "extensionsRequired"):
            if extension_key in document:
                document[extension_key] = [
                    name for name in document[extension_key]
                    if "texture" not in name.lower()
                ]
                if not document[extension_key]:
                    document.pop(extension_key)

        encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        encoded += b" " * ((4 - len(encoded) % 4) % 4)
        tail = payload[20 + json_length:]
        rebuilt = (
            struct.pack("<4sII", b"glTF", 2, 20 + len(encoded) + len(tail))
            + struct.pack("<I4s", len(encoded), b"JSON")
            + encoded + tail
        )
        glb_path.write_bytes(rebuilt)

    @staticmethod
    def _geometry_glb_is_ready(glb_path: Path) -> bool:
        try:
            payload = glb_path.read_bytes()
            magic, version, total_length = struct.unpack_from("<4sII", payload, 0)
            json_length, json_type = struct.unpack_from("<I4s", payload, 12)
            if magic != b"glTF" or version != 2 or total_length != len(payload) or json_type != b"JSON":
                return False
            document = json.loads(payload[20:20 + json_length].decode("utf-8").rstrip(" \t\r\n\x00"))
            return not document.get("images") and not document.get("textures")
        except (OSError, ValueError, UnicodeDecodeError, struct.error):
            return False

    @staticmethod
    def _geometry_material_codes(bmd0: bytes, model_id: int) -> list[dict[str, str]]:
        try:
            names = _material_texture_names(bmd0)[::2]
        except NativeMapError:
            names = ()
        return [
            {"code": f"M{model_id}.T{index:02d}", "raw_name": name.decode("ascii", "replace")}
            for index, name in enumerate(names, start=1)
        ]

    @staticmethod
    def _live_player(live: LiveMapState) -> dict[str, Any]:
        return {
            "x": live.x,
            "y": live.y,
            "elevation": live.elevation,
            "facing": live.facing,
            "movement_state": live.movement_state,
            "map_section_id": live.map_id,
            "map_name": get_map_name(live.map_id) if live.map_id is not None else None,
            "verified": live.verified,
        }

    async def _ensure_glb(self, model_path: Path, texture_path: Path, output_dir: Path, model_id: int) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(output_dir.glob("*.glb"))
        if len(existing) == 1 and self._glb_has_textures(existing[0], output_dir):
            return existing[0]
        if not self.apicula.is_file():
            raise NativeMapError("Apicula converter is missing from v2 runtime/tools/apicula")
        result = await asyncio.to_thread(
            subprocess.run,
            [
                str(self.apicula), "convert", "-f", "glb", "--overwrite",
                str(model_path), str(texture_path), "-o", str(output_dir),
            ],
            cwd=self.cache.parents[2], capture_output=True, text=True, timeout=60, check=False,
        )
        generated = sorted(output_dir.glob("*.glb"))
        if result.returncode or len(generated) != 1:
            detail = (result.stderr or result.stdout).strip() or "converter produced no single GLB"
            raise NativeMapError(f"Nitro 3D conversion failed for model {model_id}: {detail}")
        return generated[0]

    @staticmethod
    def _glb_has_textures(glb_path: Path, output_dir: Path) -> bool:
        """Accept only GLBs that contain material images and their PNG files."""
        try:
            data = glb_path.read_bytes()
            if data[:4] != b"glTF":
                return False
            json_length = struct.unpack_from("<I", data, 12)[0]
            document = json.loads(data[20:20 + json_length].decode("utf-8").rstrip(" \t\r\n\x00"))
        except (OSError, ValueError, UnicodeDecodeError, struct.error):
            return False
        images = document.get("images") or []
        textures = document.get("textures") or []
        if not images or not textures:
            return False
        return all((output_dir / image.get("uri", "")).is_file() for image in images if image.get("uri"))
