"""Fast, evidence-bounded schematic views for the live Black 2 map."""
from __future__ import annotations

from collections import Counter
import struct
from types import SimpleNamespace
from typing import Any

from ..memory.reader import MemoryReader
from .map_knowledge import _decode_events
from .native_map import NativeMapError, inspect_loaded_visual_map, read_live_map_state
from .rom_maps import NativeMapEngine, NarcArchive


_EVENTS_PATH = "a/1/2/6"
_NONE = 0xFFFFFFFF


class MapSchematicService:
    """Return map identifiers and raw evidence without creating visual assets."""

    def __init__(self) -> None:
        self._events: dict[int, dict[str, Any]] = {}

    @property
    def engine(self) -> NativeMapEngine:
        return NativeMapEngine.get_instance()

    async def current(self, reader: MemoryReader, include_raw: bool = False) -> dict[str, Any]:
        live = await read_live_map_state(reader)
        if not live.verified or live.x is None or live.y is None:
            return self._unavailable(live, "ARM9 player coordinates are not verified")
        try:
            visual = await inspect_loaded_visual_map(reader, live.x, live.y, self.engine)
        except (NativeMapError, ConnectionError, TimeoutError, OSError) as error:
            return self._unavailable(live, str(error))

        if not visual.get("player_alignment", {}).get("verified"):
            return self._unanchored(live, visual)
        return self._aligned(live, visual, include_raw)

    async def tile(self, reader: MemoryReader, x: int, y: int) -> dict[str, Any]:
        """Return raw permission bytes for one globally addressed map tile."""
        live = await read_live_map_state(reader)
        if not live.verified or live.x is None or live.y is None:
            return {"status": "unavailable", "message": "ARM9 player coordinates are not verified"}
        visual = await inspect_loaded_visual_map(reader, live.x, live.y, self.engine)
        if not visual.get("player_alignment", {}).get("verified"):
            return {
                "status": "unanchored",
                "message": "The current player-to-matrix origin is not verified.",
                "candidate_scenes": visual.get("candidate_scenes", []),
            }
        return self._tile_from_visual(x, y, visual)

    async def machine(self, reader: MemoryReader) -> dict[str, Any]:
        """Return a compact, deterministic contract for AI and pathfinding code."""
        return build_machine_map(await self.current(reader, include_raw=True))

    def visual_context(self, visual: dict[str, Any]) -> dict[str, Any]:
        """Attach a verified Map Header to an already-sampled visual scene."""
        player = visual.get("live_player") or {}
        if not player.get("verified") or not visual.get("player_alignment", {}).get("verified"):
            return {
                "map_header_id": None,
                "map_header_resolution": {
                    "verified": False,
                    "reason": "visual scene has no verified player coordinate",
                },
                "entities": self._empty_events("visual scene has no verified player coordinate"),
            }
        live = SimpleNamespace(
            x=player.get("x"),
            y=player.get("y"),
            elevation=player.get("elevation"),
            map_id=player.get("map_section_id"),
            verified=True,
        )
        aligned = self._aligned(live, visual, include_raw=False)
        return {
            "map_header_id": aligned.get("map_header_id"),
            "map_header_resolution": aligned.get("map_header_resolution"),
            # These are ROM static event definitions, not live FieldActor data.
            # Keep the raw records so renderers never need to invent NPCs, items,
            # doors, or their positions.
            "entities": aligned.get("events") or self._empty_events("no linked ROM event archive"),
        }

    def _aligned(self, live: Any, visual: dict[str, Any], include_raw: bool) -> dict[str, Any]:
        matrix_id = int(visual["matrix_id"])
        width, height, model_ids, definitions = self._matrix_tables(matrix_id)
        direct_header = visual.get("map_definition_id")
        if direct_header is None:
            map_header_id, events, header_resolution = self._standalone_interior_header(
                matrix_id, visual,
            )
        else:
            map_header_id = int(direct_header)
            events = self._events_for_header(map_header_id)
            header_resolution = {
                "verified": True,
                "method": "matrix definition table Map Header ID",
                "map_header_id": map_header_id,
            }
        active = {(int(cell["x"]), int(cell["y"])) for cell in visual.get("active_cells", [])}
        cells = []
        for y in range(height):
            for x in range(width):
                index = y * width + x
                if definitions is not None and definitions[index] != map_header_id:
                    continue
                model_id = model_ids[index]
                if model_id == _NONE:
                    continue
                model = self.engine.models.get(model_id)
                cells.append({
                    "x": x,
                    "y": y,
                    "model_id": model_id,
                    "code": f"M{model_id}",
                    "tile_size": {"width": model.width, "height": model.height} if model else None,
                    "resident": (x, y) in active,
                    "raw_permission_planes": self._raw_planes(model) if include_raw and (x, y) in active else None,
                    "raw_summary": self._raw_summary(model) if include_raw and (x, y) in active else None,
                })
        data = {
            "format": "black2-map-schematic/v1",
            "status": "aligned",
            "message": "Matrix cells are anchored by live player coordinates. Terrain labels remain raw model codes.",
            "live_player": self._live_player(live),
            "map_header_id": map_header_id,
            "map_header_resolution": header_resolution,
            "matrix": {
                "id": matrix_id,
                "width": width,
                "height": height,
                "cells": cells,
                "resident_cells": [cell for cell in cells if cell["resident"]],
            },
            "player_chunk": visual.get("player_chunk"),
            "player_local": visual.get("player_local"),
            "player_surface_projection": visual.get("player_surface_projection"),
            "chunk_tile_size": visual.get("chunk_tile_size"),
            "map_definition_bounds": visual.get("map_definition_bounds"),
            "events": events,
            "event_coordinate_space": events.get("coordinate_space", "unverified") if isinstance(events, dict) else "unverified",
            "player_tile_permission": self._player_tile_permission(live, visual),
            "raw_permissions_included": include_raw,
            "height": {
                "player_elevation_raw": live.elevation,
                "per_tile": "unverified; no per-tile height decoder is implemented",
            },
            "semantic_policy": self._semantic_policy(),
        }
        data["ai_text"] = format_schematic_text(data)
        return data

    def _tile_from_visual(self, x: int, y: int, visual: dict[str, Any]) -> dict[str, Any]:
        matrix_id = int(visual["matrix_id"])
        width, height, model_ids, definitions = self._matrix_tables(matrix_id)
        size = visual.get("chunk_tile_size") or {"width": 32, "height": 32}
        chunk_x, local_x = divmod(int(x), int(size["width"]))
        chunk_y, local_y = divmod(int(y), int(size["height"]))
        result = {
            "format": "black2-map-schematic-tile/v1",
            "status": "aligned",
            "global": {"x": int(x), "y": int(y)},
            "matrix_id": matrix_id,
            "map_header_id": visual.get("map_definition_id"),
            "chunk": {"x": chunk_x, "y": chunk_y},
            "local": {"x": local_x, "y": local_y},
            "semantic_status": "raw bytes only; blocked/passable/terrain/height is unverified",
        }
        if not (0 <= chunk_x < width and 0 <= chunk_y < height):
            result.update({"status": "outside_matrix", "message": "Global coordinate is outside the current matrix."})
            return result
        index = chunk_y * width + chunk_x
        model_id = model_ids[index]
        if model_id == _NONE or (definitions is not None and definitions[index] != visual.get("map_definition_id")):
            result.update({"status": "outside_map_definition", "message": "Global coordinate is not in the current Map Header definition."})
            return result
        model = self.engine.models.get(model_id)
        if model is None:
            result.update({"status": "model_unavailable", "model_id": model_id, "message": "The ROM model has no decoded permission block."})
            return result
        offset = local_y * model.width + local_x
        result.update({
            "model_id": model_id,
            "permission_planes": {
                f"P{plane:02d}": model.planes[plane][offset]
                for plane in range(model.plane_count)
            },
        })
        return result

    def _unanchored(self, live: Any, visual: dict[str, Any]) -> dict[str, Any]:
        scenes = []
        for scene in visual.get("candidate_scenes", []):
            scenes.append({
                "matrix_id": scene.get("matrix_id"),
                "matrix_size": scene.get("matrix_size"),
                "resident_model_ids": sorted({cell["model_id"] for cell in scene.get("resident_cells", [])}),
            })
        data = {
            "format": "black2-map-schematic/v1",
            "status": "unanchored",
            "message": "ROM models are resident, but the player-to-matrix origin is not verified. No map is drawn.",
            "live_player": self._live_player(live),
            "candidate_scenes": scenes,
            "height": {"player_elevation_raw": live.elevation, "per_tile": "unverified"},
            "semantic_policy": self._semantic_policy(),
        }
        data["ai_text"] = format_schematic_text(data)
        return data

    def _unavailable(self, live: Any, reason: str) -> dict[str, Any]:
        data = {
            "format": "black2-map-schematic/v1",
            "status": "unavailable",
            "message": reason,
            "live_player": self._live_player(live),
            "semantic_policy": self._semantic_policy(),
        }
        data["ai_text"] = format_schematic_text(data)
        return data

    def _matrix_tables(self, matrix_id: int) -> tuple[int, int, tuple[int, ...], tuple[int, ...] | None]:
        raw = self.engine.matrix_narc.files[matrix_id]
        if len(raw) < 8:
            raise NativeMapError(f"Matrix {matrix_id} is truncated")
        width, height = struct.unpack_from("<HH", raw, 4)
        count = width * height
        if not width or not height or len(raw) < 8 + count * 4:
            raise NativeMapError(f"Matrix {matrix_id} has invalid dimensions")
        models = struct.unpack_from(f"<{count}I", raw, 8)
        definitions = (
            struct.unpack_from(f"<{count}I", raw, 8 + count * 4)
            if len(raw) >= 8 + count * 8 else None
        )
        return width, height, models, definitions

    def _events_for_header(self, map_header_id: int) -> dict[str, Any]:
        offset = map_header_id * 0x30
        if offset + 0x30 > len(self.engine.zone_data):
            return {"decode_error": "Map Header is outside ZoneData"}
        event_id = struct.unpack_from("<H", self.engine.zone_data, offset + 0x10)[0]
        if event_id not in self._events:
            archive = NarcArchive(self.engine.rom.read_file(_EVENTS_PATH))
            if not 0 <= event_id < len(archive.files):
                return {"event_archive_id": event_id, "decode_error": "event archive is outside ROM data"}
            self._events[event_id] = _decode_events(archive.files[event_id], event_id)
        return self._events[event_id]

    def _standalone_interior_header(
        self, matrix_id: int, visual: dict[str, Any],
    ) -> tuple[int | None, dict[str, Any], dict[str, Any]]:
        """Resolve an interior only through one exact ZoneData primary link."""
        header_ids = []
        for header_id in range(len(self.engine.zone_data) // 0x30):
            offset = header_id * 0x30
            primary, shared = struct.unpack_from("<HH", self.engine.zone_data, offset)
            if primary == matrix_id and shared == 0:
                header_ids.append(header_id)
        resolution = {
            "verified": False,
            "method": "unique ZoneData primary interior-matrix reference + in-bounds events",
            "matrix_id": matrix_id,
            "candidate_map_header_ids": header_ids,
        }
        if len(header_ids) != 1:
            resolution["reason"] = (
                "no Map Header references this standalone interior matrix"
                if not header_ids else
                "multiple Map Headers reference this standalone interior matrix"
            )
            return None, self._empty_events(resolution["reason"]), resolution

        header_id = header_ids[0]
        events = self._events_for_header(header_id)
        bounds = visual.get("map_definition_bounds") or {}
        size = visual.get("chunk_tile_size") or {"width": 32, "height": 32}
        tile_width = int(bounds.get("width", 0)) * int(size["width"])
        tile_height = int(bounds.get("height", 0)) * int(size["height"])
        records = [
            record
            for group in ("furniture", "npcs", "warps", "triggers")
            for record in events.get(group, [])
        ]
        in_bounds = tile_width > 0 and tile_height > 0 and all(
            0 <= float(record["tile_x"]) < tile_width
            and 0 <= float(record["tile_y"]) < tile_height
            for record in records
        )
        if not in_bounds:
            resolution["reason"] = "the linked event record falls outside the complete interior matrix"
            return None, self._empty_events(resolution["reason"]), resolution
        resolution.update({
            "verified": True,
            "map_header_id": header_id,
            "event_archive_id": events.get("event_archive_id"),
        })
        return header_id, events, resolution

    @staticmethod
    def _empty_events(reason: str) -> dict[str, Any]:
        return {
            "coordinate_space": "unverified",
            "furniture": [],
            "npcs": [],
            "warps": [],
            "triggers": [],
            "counts": {"furniture": 0, "npcs": 0, "warps": 0, "triggers": 0},
            "decode_error": reason,
        }

    @staticmethod
    def _raw_planes(model: Any) -> dict[str, list[list[int]]] | None:
        if model is None:
            return None
        return {str(index): model.plane_rows(index) for index in range(model.plane_count)}

    @staticmethod
    def _raw_summary(model: Any) -> dict[str, Any] | None:
        """Summarize raw permission bytes without assigning gameplay meaning."""
        if model is None:
            return None
        values = [value for plane in model.planes for value in plane]
        occupied = sum(
            any(model.planes[plane][row * model.width + column] != 0 for plane in range(model.plane_count))
            for row in range(model.height)
            for column in range(model.width)
        )
        return {
            "grid": {"width": model.width, "height": model.height},
            "nonzero_cells_any_plane": occupied,
            "nonzero_bytes_by_plane": [sum(value != 0 for value in plane) for plane in model.planes],
            "value_counts_all_planes": {
                f"0x{value:02X}": count
                for value, count in sorted(Counter(values).items())
                if value != 0
            },
        }

    def _player_tile_permission(self, live: Any, visual: dict[str, Any]) -> dict[str, Any] | None:
        """Expose the exact current tile bytes, without interpreting them."""
        model_id = visual.get("player_model_id")
        model = self.engine.models.get(model_id)
        if model is None or live.x is None or live.y is None:
            return None
        local_x, local_y = live.x % model.width, live.y % model.height
        offset = local_y * model.width + local_x
        return {
            "global": {"x": live.x, "y": live.y},
            "model_id": model_id,
            "local": {"x": local_x, "y": local_y},
            "permission_planes": {
                f"P{plane:02d}": model.planes[plane][offset]
                for plane in range(model.plane_count)
            },
            "semantic_status": "raw bytes only; blocked/passable/terrain/height is unverified",
        }

    @staticmethod
    def _live_player(live: Any) -> dict[str, Any]:
        return {
            "x": live.x,
            "y": live.y,
            "elevation": live.elevation,
            "map_section_id": live.map_id,
            "verified": live.verified,
        }

    @staticmethod
    def _semantic_policy() -> dict[str, str]:
        return {
            "model_codes": "M<number> is a ROM model identifier, not a terrain name",
            "raw_permissions": "Pxx values are raw bytes, not wall/road/grass/passability labels",
            "height": "only the live player elevation is shown; per-tile height is unverified",
            "events": "warp/NPC/furniture/trigger records are indexed, but item and field-move meanings are not inferred",
        }


def format_schematic_text(data: dict[str, Any]) -> str:
    """Compact text contract for an AI consumer of the live schematic."""
    player = data["live_player"]
    lines = [
        "BLACK2_MAP_SCHEMATIC/v1",
        f"STATUS={data['status']}",
        f"PLAYER x={player.get('x')} y={player.get('y')} elevation_raw={player.get('elevation')} verified={player.get('verified')}",
        f"MESSAGE={data['message']}",
    ]
    matrix = data.get("matrix")
    if matrix:
        lines.append(f"MAP_HEADER={data.get('map_header_id')} MATRIX={matrix['id']} SIZE={matrix['width']}x{matrix['height']}")
        lines.append(f"PLAYER_CHUNK={data.get('player_chunk')} PLAYER_LOCAL={data.get('player_local')}")
        for cell in matrix["cells"]:
            size = cell.get("tile_size") or {}
            lines.append(
                f"CELL x={cell['x']} y={cell['y']} code={cell['code']} resident={cell['resident']} "
                f"tile_size={size.get('width')}x{size.get('height')}"
            )
            summary = cell.get("raw_summary")
            if summary:
                lines.append(
                    f"RAW_CELL x={cell['x']} y={cell['y']} grid={summary['grid']['width']}x{summary['grid']['height']} "
                    f"nonzero_any_plane={summary['nonzero_cells_any_plane']}"
                )
        counts = data.get("events", {}).get("counts", {})
        lines.append(
            "EVENTS " + " ".join(f"{name}={counts.get(name, 0)}" for name in ("warps", "npcs", "furniture", "triggers"))
        )
        lines.append(f"EVENT_COORDINATES={data.get('event_coordinate_space', 'unverified')}")
        tile = data.get("player_tile_permission")
        if tile:
            values = " ".join(f"{key}=0x{int(value):02X}" for key, value in tile["permission_planes"].items())
            lines.append(
                f"PLAYER_TILE global=({tile['global']['x']},{tile['global']['y']}) model=M{tile['model_id']} "
                f"local=({tile['local']['x']},{tile['local']['y']}) {values}"
            )
    for key, value in data["semantic_policy"].items():
        lines.append(f"BOUNDARY {key}={value}")
    return "\n".join(lines) + "\n"


def build_machine_map(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten verified map coordinates without inventing navigation semantics."""
    player = data.get("live_player") or {}
    occupied_tile = (
        [{
            "x": int(player["x"]),
            "y": int(player["y"]),
            "evidence": "verified live player occupancy",
        }]
        if player.get("verified") and player.get("x") is not None and player.get("y") is not None
        else []
    )
    result = {
        "format": "black2-ai-map/v1",
        "status": data.get("status"),
        "message": data.get("message"),
        "coordinate_system": {
            "tile_axes": "global ROM tile x/y",
            "chunk_axes": "matrix x/y",
            "chunk_origin": "global tile = chunk * chunk_tile_size + local tile",
        },
        "player": player,
        "player_tile": data.get("player_tile_permission"),
        "map_header_id": data.get("map_header_id"),
        "map_header_resolution": data.get("map_header_resolution"),
        "navigation": {
            "route_planning_ready": False,
            "walkability_field": None,
            "known_walkable_tiles": occupied_tile,
            "known_blocked_tiles": [],
            "reason": "Pxx bytes are preserved, but their blocked/passable meanings are not verified.",
        },
        "semantic_policy": data.get("semantic_policy", {}),
    }
    if data.get("status") != "aligned":
        result["candidates"] = data.get("candidate_scenes", [])
        return result

    size = data.get("chunk_tile_size") or {"width": 32, "height": 32}
    plane_count = max(
        (
            len(cell.get("raw_permission_planes") or {})
            for cell in data.get("matrix", {}).get("resident_cells", [])
        ),
        default=0,
    )
    schema = ["global_x", "global_y", "model_id"] + [
        f"P{plane:02d}" for plane in range(plane_count)
    ]
    records = []
    for cell in data.get("matrix", {}).get("resident_cells", []):
        planes = cell.get("raw_permission_planes") or {}
        tile_size = cell.get("tile_size") or size
        for local_y in range(int(tile_size["height"])):
            for local_x in range(int(tile_size["width"])):
                records.append([
                    int(cell["x"]) * int(tile_size["width"]) + local_x,
                    int(cell["y"]) * int(tile_size["height"]) + local_y,
                    int(cell["model_id"]),
                    *[
                        int(planes.get(str(plane), [])[local_y][local_x])
                        if local_y < len(planes.get(str(plane), []))
                        and local_x < len(planes.get(str(plane), [])[local_y])
                        else 0
                        for plane in range(plane_count)
                    ],
                ])
    result.update({
        "matrix_id": data.get("matrix", {}).get("id"),
        "chunk_tile_size": size,
        "map_definition_bounds": data.get("map_definition_bounds"),
        "tile_record_schema": schema,
        "tile_record_count": len(records),
        "tile_records": records,
        "events": {
            "coordinate_space": data.get("event_coordinate_space", "unverified"),
            **{
                group: data.get("events", {}).get(group, [])
                for group in ("warps", "npcs", "furniture", "triggers")
            },
        },
    })
    return result
