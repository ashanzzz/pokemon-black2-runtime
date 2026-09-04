"""Authoritative current-map join: runtime Field structures + Black 2 ROM data.

The service deliberately separates three layers:

1. runtime truth discovered from the current Main RAM (Field/Player/Actors/Mapper/Props),
2. static ROM truth (matrix, map header, model permission planes, events), and
3. semantic interpretation, which stays unresolved until behavioral evidence exists.

It never uses the legacy hand-authored POI/map database as a source of current
world state.
"""
from __future__ import annotations

from collections import Counter
import struct
from typing import Any

from ..memory.reader import MemoryReader
from .map_knowledge import MapKnowledgeService
from .rom_maps import NativeMapEngine
from .rom_reader import NarcArchive
from .runtime_field_resolver import read_main_ram, resolve_runtime_field_from_ram

_NONE = 0xFFFFFFFF
_TEXTURE_PATH = "a/0/1/4"


def _matrix_tables(raw: bytes) -> tuple[int, int, tuple[int, ...], tuple[int, ...] | None]:
    if len(raw) < 8:
        raise ValueError("matrix is truncated")
    width, height = struct.unpack_from("<HH", raw, 4)
    count = width * height
    if not width or not height or len(raw) < 8 + count * 4:
        raise ValueError("matrix dimensions are invalid")
    models = struct.unpack_from(f"<{count}I", raw, 8)
    definitions = (
        struct.unpack_from(f"<{count}I", raw, 8 + count * 4)
        if len(raw) >= 8 + count * 8 else None
    )
    return width, height, models, definitions


def _match_runtime_matrix(runtime: dict[str, Any], engine: NativeMapEngine) -> dict[str, Any]:
    mapper = runtime.get("mapper", {})
    width = int(mapper.get("matrix_width") or 0)
    height = int(mapper.get("matrix_height") or 0)
    runtime_ids = tuple(int(v) for v in mapper.get("chunk_dat_ids", []))
    if not width or not height or len(runtime_ids) != width * height:
        return {
            "value": None,
            "confidence": "unresolved",
            "reason": "runtime FieldG3DMapper has no complete ChunkDatIDs table",
            "matches": [],
        }
    exact: list[int] = []
    dimension_only: list[int] = []
    for matrix_id, raw in enumerate(engine.matrix_narc.files):
        try:
            rw, rh, models, _defs = _matrix_tables(raw)
        except (ValueError, struct.error):
            continue
        if (rw, rh) != (width, height):
            continue
        dimension_only.append(matrix_id)
        if tuple(models) == runtime_ids:
            exact.append(matrix_id)
    if len(exact) == 1:
        return {
            "value": exact[0],
            "confidence": "probable",
            "reason": "runtime ChunkDatIDs exactly equal one ROM matrix model table",
            "matches": exact,
            "dimension_matches": dimension_only,
        }
    if exact:
        return {
            "value": None,
            "confidence": "candidate",
            "reason": "runtime ChunkDatIDs exactly match multiple ROM matrices",
            "matches": exact,
            "dimension_matches": dimension_only,
        }
    return {
        "value": None,
        "confidence": "unresolved",
        "reason": "no ROM matrix exactly matches runtime ChunkDatIDs",
        "matches": [],
        "dimension_matches": dimension_only,
    }


def _header_from_matrix(
    engine: NativeMapEngine,
    matrix_id: int | None,
    player_chunk_index: int | None,
) -> dict[str, Any]:
    if matrix_id is None or player_chunk_index is None:
        return {"value": None, "confidence": "unresolved", "reason": "matrix/player chunk unavailable"}
    try:
        width, height, _models, definitions = _matrix_tables(engine.matrix_narc.files[matrix_id])
    except (IndexError, ValueError, struct.error) as exc:
        return {"value": None, "confidence": "unresolved", "reason": str(exc)}
    count = width * height
    if not 0 <= player_chunk_index < count:
        return {"value": None, "confidence": "unresolved", "reason": "player chunk outside ROM matrix"}
    if definitions is not None:
        value = int(definitions[player_chunk_index])
        if value != _NONE:
            return {
                "value": value,
                "confidence": "probable",
                "reason": "current runtime player chunk selects this Map Header in the exact ROM matrix definition table",
            }
    # Standalone interiors often omit the definition table.  Accept only a
    # unique primary ZoneData reference; shared/global matrix references are
    # not enough to identify an interior.
    candidates = []
    for header_id in range(len(engine.zone_data) // 0x30):
        off = header_id * 0x30
        primary, shared = struct.unpack_from("<HH", engine.zone_data, off)
        if primary == matrix_id and shared == 0:
            candidates.append(header_id)
    if len(candidates) == 1:
        return {
            "value": candidates[0],
            "confidence": "probable",
            "reason": "standalone matrix has one unique primary ZoneData owner",
        }
    return {
        "value": None,
        "confidence": "candidate" if candidates else "unresolved",
        "reason": "standalone matrix Map Header is not unique" if candidates else "matrix has no current definition and no unique ZoneData owner",
        "candidates": candidates,
    }


def _resolve_header_identity(
    runtime: dict[str, Any],
    matrix_header: dict[str, Any],
) -> dict[str, Any]:
    player_zone = runtime.get("player", {}).get("zone_id")
    actor_zone = runtime.get("actors", {}).get("zone_consensus", {}).get("value")
    matrix_zone = matrix_header.get("value")
    signals = {
        "player_state_zone_id": player_zone,
        "actor_zone_consensus": actor_zone,
        "matrix_definition_map_header": matrix_zone,
    }
    values = [int(v) for v in signals.values() if isinstance(v, int) and v != _NONE]
    counts = Counter(values)
    if not counts:
        return {
            "value": None,
            "confidence": "unresolved",
            "signals": signals,
            "reason": "no independent runtime/ROM Map Header signal is available",
        }
    value, votes = counts.most_common(1)[0]
    disagreements = sorted({v for v in values if v != value})
    if votes >= 2 and not disagreements:
        confidence = "probable"
        reason = f"{votes} independent runtime/ROM signals agree on Map Header/ZoneID"
    elif votes >= 2:
        confidence = "candidate"
        reason = "multiple signals agree but at least one signal disagrees"
    else:
        confidence = "candidate"
        reason = "only one Map Header/ZoneID signal is currently available"
    return {
        "value": value,
        "confidence": confidence,
        "signals": signals,
        "votes": votes,
        "disagreements": disagreements,
        "reason": reason,
    }


def _resident_texture_ids(ram: bytes, engine: NativeMapEngine) -> dict[str, Any]:
    archive = NarcArchive(engine.rom.read_file(_TEXTURE_PATH))
    prefixes: dict[bytes, list[int]] = {}
    for texture_id, payload in enumerate(archive.files):
        if len(payload) >= 16 and payload[:4] == b"BTX0":
            prefixes.setdefault(payload[:16], []).append(texture_id)
    found: set[int] = set()
    offsets: dict[int, list[str]] = {}
    cursor = 0
    while True:
        pos = ram.find(b"BTX0", cursor)
        if pos < 0:
            break
        cursor = pos + 4
        ids = prefixes.get(ram[pos:pos + 16], [])
        for texture_id in ids:
            found.add(texture_id)
            offsets.setdefault(texture_id, []).append(f"0x{0x02000000 + pos:08X}")
    return {
        "ids": sorted(found),
        "confidence": "probable" if len(found) == 1 else ("candidate" if found else "unresolved"),
        "ram_offsets": {str(k): v for k, v in sorted(offsets.items())},
        "source": "BTX0 16-byte prefixes currently resident in Main RAM matched to rom:/a/0/1/4",
    }


def _player_permission(runtime: dict[str, Any], engine: NativeMapEngine) -> dict[str, Any]:
    mapper = runtime.get("mapper", {})
    actor = runtime.get("player", {}).get("actor") or {}
    model_id = mapper.get("player_model_id")
    size = mapper.get("chunk_tile_size")
    gpos = actor.get("grid_position") or {}
    if not isinstance(model_id, int) or model_id not in engine.models or not size:
        return {"status": "unresolved", "reason": "player model/chunk size has no decoded ROM permission model"}
    model = engine.models[model_id]
    try:
        local_x = int(gpos["x"]) % int(size)
        local_y = int(gpos["z"]) % int(size)
    except (KeyError, TypeError, ValueError):
        return {"status": "unresolved", "reason": "player GPos is unavailable"}
    if not (0 <= local_x < model.width and 0 <= local_y < model.height):
        return {"status": "unresolved", "reason": "player local tile is outside decoded permission dimensions"}
    index = local_y * model.width + local_x
    return {
        "status": "probable",
        "model_id": model_id,
        "local_tile": {"x": local_x, "y": local_y},
        "permission_planes": {
            f"P{plane:02d}": int(model.planes[plane][index])
            for plane in range(model.plane_count)
        },
        "runtime_tile_type": actor.get("tile_under"),
        "semantic_status": "raw ROM permission bytes correlated with runtime TileType; passability/terrain/height meaning is not inferred",
    }


def _candidate_npc_links(runtime_actors: list[dict[str, Any]], static_npcs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose only unique script-ID joins; do not call them verified identities."""
    by_script: dict[int, list[dict[str, Any]]] = {}
    for npc in static_npcs:
        script_id = npc.get("script_id")
        if isinstance(script_id, int):
            by_script.setdefault(script_id, []).append(npc)
    links = []
    for actor in runtime_actors:
        if actor.get("is_player"):
            continue
        script_id = actor.get("script_id")
        matches = by_script.get(script_id, []) if isinstance(script_id, int) else []
        if len(matches) == 1:
            links.append({
                "runtime_actor_slot": actor.get("slot"),
                "runtime_actor_uid": actor.get("actor_uid"),
                "script_id": script_id,
                "static_npc_id": matches[0].get("id"),
                "confidence": "candidate",
                "reason": "unique script_id equality only; current-position/spawn-position coherence still needs validation",
            })
    return links


class MapTruthService:
    """Join current Field runtime objects to the exact matching Black 2 ROM map."""

    def __init__(self) -> None:
        self.knowledge = MapKnowledgeService()

    @property
    def engine(self) -> NativeMapEngine:
        return NativeMapEngine.get_instance()

    async def current(self, reader: MemoryReader, include_raw: bool = False) -> dict[str, Any]:
        # A truth request is an explicit RE operation.  One full RAM read lets
        # every runtime object and loaded resource be judged at one sampling pass
        # rather than mixing a collection of legacy fixed-address mirrors.
        ram = await read_main_ram(reader)
        runtime = resolve_runtime_field_from_ram(ram)
        if runtime.get("status") not in {"resolved", "candidate"}:
            return {
                "format": "black2-map-truth/v2",
                "status": "unresolved",
                "confidence": "unresolved",
                "runtime": runtime,
                "reason": "current Field runtime could not be structurally resolved",
            }

        matrix_identity = _match_runtime_matrix(runtime, self.engine)
        matrix_id = matrix_identity.get("value") if isinstance(matrix_identity.get("value"), int) else None
        player_chunk = runtime.get("mapper", {}).get("player_chunk") or {}
        matrix_header = _header_from_matrix(
            self.engine,
            matrix_id,
            player_chunk.get("index") if isinstance(player_chunk, dict) else None,
        )
        header_identity = _resolve_header_identity(runtime, matrix_header)
        header_id = header_identity.get("value") if isinstance(header_identity.get("value"), int) else None

        rom_detail = None
        rom_error = None
        if header_id is not None:
            try:
                rom_detail = self.knowledge.map_detail(header_id, include_raw=include_raw)
            except (ValueError, OSError, struct.error) as exc:
                rom_error = f"{type(exc).__name__}: {exc}"

        runtime_actors = runtime.get("actors", {}).get("actors", []) or []
        static_npcs = (rom_detail or {}).get("events", {}).get("npcs", []) or []
        texture_identity = _resident_texture_ids(ram, self.engine)
        permission = _player_permission(runtime, self.engine)

        selected_cells = []
        model_ids: set[int] = set()
        if rom_detail:
            for matrix in rom_detail.get("matrices", []):
                if matrix.get("matrix_id") == matrix_id:
                    selected_cells = list(matrix.get("selected_cells", []))
                    model_ids.update(
                        int(cell["model_id"]) for cell in selected_cells
                        if isinstance(cell.get("model_id"), int) and cell.get("model_id") != _NONE
                    )
        if not model_ids:
            model_ids.update(
                int(item.get("model_id")) for item in runtime.get("mapper", {}).get("loaded_chunks", [])
                if isinstance(item.get("model_id"), int) and item.get("model_id") != _NONE
            )
        models = []
        for model_id in sorted(model_ids):
            model = self.engine.models.get(model_id)
            models.append({
                "model_id": model_id,
                "source": f"rom:/a/0/0/8[{model_id}]",
                "permission_grid": (
                    {"width": model.width, "height": model.height, "planes": model.plane_count}
                    if model else None
                ),
                "semantic_status": "BMD0/map-model resource; building/road/object names are not inferred from geometry",
            })

        events = (rom_detail or {}).get("events") or {
            "furniture": [], "npcs": [], "warps": [], "triggers": [],
            "counts": {"furniture": 0, "npcs": 0, "warps": 0, "triggers": 0},
        }
        confidence = "probable" if all((
            runtime.get("confidence") == "probable",
            matrix_identity.get("confidence") == "probable",
            header_identity.get("confidence") == "probable",
        )) else "candidate"
        return {
            "format": "black2-map-truth/v2",
            "status": "resolved",
            "confidence": confidence,
            "authority": {
                "runtime": "current Main RAM Field/Player/ActorSystem/G3DMapper/FieldPropSystem structures",
                "static": "current runtime matrix/header joined to Black 2 ROM NARCs",
                "manual_map_database_used": False,
                "screenshot_used_as_truth": False,
                "writes_to_game_memory": False,
            },
            "identity": {
                "matrix": matrix_identity,
                "map_header": header_identity,
                "matrix_definition_signal": matrix_header,
                "resident_textures": texture_identity,
            },
            "player": runtime.get("player"),
            "streaming": {
                "mapper": {
                    key: runtime.get("mapper", {}).get(key) for key in (
                        "address", "matrix_width", "matrix_height", "chunk_id_count",
                        "chunk_span_world", "chunk_tile_size", "load_diameter",
                        "chunk_capacity", "player_chunk", "player_model_id",
                    )
                },
                "loaded_chunks": runtime.get("mapper", {}).get("loaded_chunks", []),
                "full_current_map_cells": selected_cells,
            },
            "runtime_actors": runtime_actors,
            "runtime_actor_system": {
                key: runtime.get("actors", {}).get(key) for key in (
                    "address", "capacity", "declared_count", "resolved_count",
                    "player_slot", "structure_coherent", "zone_consensus",
                )
            },
            "runtime_props": runtime.get("props"),
            "rom_events": events,
            "candidate_npc_links": _candidate_npc_links(runtime_actors, static_npcs),
            "collision": {
                "player_tile": permission,
                "current_map_models": models,
                "semantic_status": "raw permission planes retained; blocked/passable/slope/water semantics require controlled movement evidence",
            },
            "assets": {
                "map_models": models,
                "resident_texture_ids": texture_identity.get("ids", []),
                "native_render_api": "/api/v1/map/visual",
                "geometry_only_api": "/api/v1/map/geometry",
                "note": "buildings and terrain remain actual BMD0 geometry; names are not synthesized",
            },
            "rom_detail": rom_detail,
            "rom_error": rom_error,
            "runtime_evidence": runtime,
            "next_validation": [
                "capture the same scene after one-tile moves to promote Field/Player/Mapper offsets across snapshots",
                "capture before/after one door or warp to validate raw warp target and DoorUID relationships",
                "capture a moving NPC twice to link ROM spawn records to live FieldActor positions",
                "capture passable and blocked input pairs to decode raw permission/TileType semantics",
            ],
        }
