"""UI-oriented map scene model built from MapTruth without inventing semantics."""
from __future__ import annotations

import asyncio
import copy
import math
import re
import struct
import time
from dataclasses import dataclass, field
from typing import Any

from ..memory.reader import MemoryReader
from .map_truth import MapTruthService

_MODEL_NAME_RE = re.compile(rb"m_[A-Za-z0-9_]{2,}")
_WORLD_UNITS_PER_TILE = 16.0


def _embedded_bmd(payload: bytes) -> bytes | None:
    if len(payload) < 16:
        return None
    try:
        _sig, start, end, _total = struct.unpack_from("<4I", payload)
    except struct.error:
        return None
    if 16 <= start < end <= len(payload) and payload[start:start + 4] == b"BMD0":
        return payload[start:end]
    if payload[:4] == b"BMD0":
        return payload
    return None


def _model_name(payload: bytes) -> str | None:
    bmd = _embedded_bmd(payload)
    if not bmd:
        return None
    names = sorted(set(_MODEL_NAME_RE.findall(bmd)))
    return names[0].decode("ascii", errors="replace") if names else None


def _point_distance(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    try:
        dx = float(a["x"]) - float(b["x"])
        dy = float(a["y"]) - float(b["y"])
    except (KeyError, TypeError, ValueError):
        return None
    return math.hypot(dx, dy)


def _scene_from_truth(truth: dict[str, Any], service: MapTruthService) -> dict[str, Any]:
    if truth.get("status") != "resolved":
        return {
            "format": "black2-map-scene/v1",
            "status": truth.get("status", "unresolved"),
            "confidence": truth.get("confidence", "unresolved"),
            "reason": truth.get("reason", "map truth unresolved"),
            "truth": truth,
        }

    streaming = truth.get("streaming") or {}
    mapper = streaming.get("mapper") or {}
    tile_size = int(mapper.get("chunk_tile_size") or 32)
    cells = list(streaming.get("full_current_map_cells") or [])
    if not cells:
        cells = [
            {
                "x": (item.get("cell") or {}).get("x"),
                "y": (item.get("cell") or {}).get("y"),
                "model_id": item.get("model_id"),
            }
            for item in streaming.get("loaded_chunks") or []
            if isinstance(item.get("cell"), dict)
        ]
    valid_cells = [
        c for c in cells
        if isinstance(c.get("x"), int) and isinstance(c.get("y"), int)
    ]
    origin = None
    if valid_cells:
        min_x = min(int(c["x"]) for c in valid_cells)
        min_y = min(int(c["y"]) for c in valid_cells)
        origin = {
            "chunk_x": min_x,
            "chunk_y": min_y,
            "global_tile_x": min_x * tile_size,
            "global_tile_y": min_y * tile_size,
            "basis": "minimum cell of the current ROM Map Header definition",
        }

    model_name_cache: dict[int, str | None] = {}
    model_cells = []
    for cell in valid_cells:
        model_id = cell.get("model_id")
        if not isinstance(model_id, int) or model_id == 0xFFFFFFFF:
            continue
        if model_id not in model_name_cache:
            try:
                payload = service.engine.model_narc.files[model_id]
                model_name_cache[model_id] = _model_name(payload)
            except (IndexError, TypeError):
                model_name_cache[model_id] = None
        model_cells.append({
            "chunk": {"x": cell["x"], "y": cell["y"]},
            "model_id": model_id,
            "model_name": model_name_cache[model_id],
            "source": f"rom:/a/0/0/8[{model_id}]",
            "semantic_role": "map_geometry",
        })

    props = truth.get("runtime_props") or {}
    prop_instances = []
    door_candidates = []
    for item in props.get("instances") or []:
        world = item.get("absolute_world") or {}
        global_tile = None
        local_tile = None
        if isinstance(world.get("x"), (int, float)) and isinstance(world.get("z"), (int, float)):
            global_tile = {
                "x": float(world["x"]) / _WORLD_UNITS_PER_TILE,
                "y": float(world["z"]) / _WORLD_UNITS_PER_TILE,
            }
            if origin:
                local_tile = {
                    "x": global_tile["x"] - origin["global_tile_x"],
                    "y": global_tile["y"] - origin["global_tile_y"],
                }
        row = {
            **item,
            "global_tile_estimate": global_tile,
            "map_local_tile_estimate": local_tile,
            "coordinate_note": (
                "FieldPropInstance absolute WPos / 16, then shifted by current Map Header cell origin"
                if local_tile else "map-local transform unresolved"
            ),
            "semantic_role": "door_candidate" if item.get("has_door_metadata") else "runtime_prop",
        }
        prop_instances.append(row)
        if item.get("has_door_metadata"):
            door_candidates.append(row)

    events = truth.get("rom_events") or {}
    warps = []
    for warp in events.get("warps") or []:
        warps.append({
            **warp,
            "map_local_tile": {"x": warp.get("tile_x"), "y": warp.get("tile_y")},
            "semantic_role": "warp_region",
            "destination_status": "raw target IDs; destination landing requires live transition validation",
        })

    links = []
    for door_idx, door in enumerate(door_candidates):
        door_pt = door.get("map_local_tile_estimate")
        if not door_pt:
            continue
        candidates = []
        for warp in warps:
            distance = _point_distance(door_pt, warp.get("map_local_tile") or {})
            if distance is not None and distance <= 2.0:
                candidates.append((distance, warp))
        candidates.sort(key=lambda x: x[0])
        for distance, warp in candidates[:3]:
            links.append({
                "door_index": door_idx,
                "door_uid": door.get("door_uid"),
                "resource_uid": door.get("resource_uid"),
                "warp_id": warp.get("id"),
                "target_map_id_raw": warp.get("target_map_id"),
                "target_warp_id_raw": warp.get("target_warp_id"),
                "distance_tiles": round(distance, 4),
                "confidence": "candidate",
                "reason": (
                    "runtime DoorUID prop and ROM warp occupy nearby coordinates after the explicit "
                    "Map Header origin transform; transition evidence is still required"
                ),
            })

    actors = truth.get("runtime_actors") or []
    rom_npcs = events.get("npcs") or []
    return {
        "format": "black2-map-scene/v1",
        "status": "resolved",
        "confidence": truth.get("confidence", "candidate"),
        "identity": truth.get("identity"),
        "coordinate_system": {
            "world_units_per_tile": _WORLD_UNITS_PER_TILE,
            "chunk_tile_size": tile_size,
            "map_definition_origin": origin,
        },
        "player": truth.get("player"),
        "geometry": {
            "cells": model_cells,
            "model_count": len({c["model_id"] for c in model_cells}),
            "cell_count": len(model_cells),
            "note": (
                "BMD0 cells are the game's actual map geometry. Individual visible buildings may be "
                "part of a cell mesh and therefore do not necessarily have a standalone building object."
            ),
        },
        "structures": {
            "runtime_prop_count": len(prop_instances),
            "runtime_props": prop_instances,
            "door_candidate_count": len(door_candidates),
            "door_candidates": door_candidates,
            "warp_count": len(warps),
            "warps": warps,
            "door_warp_candidate_links": links,
            "building_semantics": (
                "not synthesized: use BMD0 geometry + Prop/door/warp evidence; names/types require "
                "resource/script/text or controlled transition evidence"
            ),
        },
        "actors": {
            "runtime": actors,
            "rom_static_npcs": rom_npcs,
            "candidate_links": truth.get("candidate_npc_links") or [],
        },
        "collision": truth.get("collision"),
        "sources": truth.get("authority"),
        "truth_status": {
            "matrix": (truth.get("identity") or {}).get("matrix"),
            "map_header": (truth.get("identity") or {}).get("map_header"),
            "runtime_props": props.get("status"),
        },
        "next_evidence": truth.get("next_validation") or [],
    }


@dataclass
class MapSceneService:
    truth: MapTruthService = field(default_factory=MapTruthService)
    cache_seconds: float = 3.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _cached: dict[str, Any] | None = field(default=None, init=False)
    _cached_at: float = field(default=0.0, init=False)

    async def current(self, reader: MemoryReader, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._cached is not None and now - self._cached_at < self.cache_seconds:
            result = copy.deepcopy(self._cached)
            result["cache"] = {"hit": True, "age_seconds": now - self._cached_at}
            return result
        async with self._lock:
            now = time.monotonic()
            if not force and self._cached is not None and now - self._cached_at < self.cache_seconds:
                result = copy.deepcopy(self._cached)
                result["cache"] = {"hit": True, "age_seconds": now - self._cached_at}
                return result
            truth = await self.truth.current(reader, include_raw=False)
            scene = _scene_from_truth(truth, self.truth)
            self._cached = scene
            self._cached_at = time.monotonic()
            result = copy.deepcopy(scene)
            result["cache"] = {"hit": False, "age_seconds": 0.0}
            return result
