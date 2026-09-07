"""Bounded AI map views, using the same strict ROM readers as the 3D scene."""
from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from typing import Any

from .gen5_rom_map import MATRIX_NONE, Gen5RomMap


GRID_SPACE = "gen5-field-grid-v1"
CHUNK_TILES = 32


class SemanticWorldService:
    def __init__(self, rom: Gen5RomMap) -> None:
        self.rom = rom

    @lru_cache(maxsize=256)
    def terrain_grids(self, chunk_id: int):
        from .tile_semantics import decode_chunk_terrain
        return decode_chunk_terrain(self.rom.chunk(chunk_id))

    def zone(self, zone_id: int) -> dict[str, Any]:
        header = self.rom.zone(zone_id)
        matrix = self.rom.matrix(header.matrix_id)
        cells = [
            {"x": cell["x"], "z": cell["y"], "chunk_id": cell["chunk_id"]}
            for cell in matrix.cells()
            if cell["chunk_id"] != MATRIX_NONE and cell["zone_id"] in (None, zone_id)
        ]
        return {
            "zone_id": zone_id,
            "header": asdict(header),
            "matrix": {"id": matrix.matrix_id, "width": matrix.width, "height": matrix.height, "cells": cells},
            "coordinate_space": GRID_SPACE,
            "chunk_tiles": CHUNK_TILES,
            "events": self.rom.entities(header.entities_id),
            "rules": {name.removeprefix("enable_"): getattr(header, name) for name in (
                "enable_cycling", "enable_running", "enable_escape_rope", "enable_fly_from", "enable_entralink_warp",
            )},
            "source": f"rom:/a/0/1/2[0]/zone/{zone_id}",
            "coverage": {"static": "rom_zone", "live_actor_positions": "not_in_static_events"},
        }

    def tile(self, zone_id: int, x: int, y: int, z: int, *, include_raw: bool = False) -> dict[str, Any]:
        header = self.rom.zone(zone_id)
        matrix = self.rom.matrix(header.matrix_id)
        cx, lx = divmod(x, CHUNK_TILES)
        cz, lz = divmod(z, CHUNK_TILES)
        result = {
            "format": "black2-ai-tile/v2", "status": "unresolved",
            "coordinate": {"space": GRID_SPACE, "zone_id": zone_id, "x": x, "y": y, "z": z},
            "terrain": {"kind": "unknown", "label": None, "status": "unverified"},
            "collision": {"can_walk": None, "status": "unverified"},
            "surfaces": [], "interactions": [],
            "rom": {"matrix_id": matrix.matrix_id, "chunk": {"x": cx, "z": cz}, "local": {"x": lx, "z": lz}},
            "evidence": {"source": "ROM terrain records", "verified": False, "confidence": "unverified"},
        }
        if not (0 <= cx < matrix.width and 0 <= cz < matrix.height):
            result["status"] = "outside_matrix"
            return result
        cell = matrix.cell(cx, cz)
        if cell["zone_id"] not in (None, zone_id):
            result["status"] = "outside_zone"
            result["rom"]["owner_zone_id"] = cell["zone_id"]
            return result
        if cell["chunk_id"] == MATRIX_NONE:
            result["status"] = "empty_chunk"
            return result
        chunk_id = int(cell["chunk_id"])
        result["rom"].update({"chunk_id": chunk_id, "model_id": chunk_id})
        try:
            grids = self.terrain_grids(chunk_id)
        except (IndexError, ValueError) as error:
            result.update(status="terrain_decode_error", error=str(error))
            return result
        result["surfaces"] = self._surfaces(grids, lx, lz, include_raw)
        result["height"] = {"requested_y": y, "status": "unresolved", "selected_surface": None,
                            "reason": "ROM heights are relative to a chunk origin; floor selection requires alignment."}
        result["projected_materials"] = [surface["material"] for surface in result["surfaces"]]
        result["status"] = "decoded" if result["surfaces"] else "terrain_unavailable"
        result["evidence"]["record_layout"] = "interleaved tile records; never legacy P00 byte planes"
        return result

    @staticmethod
    def _surfaces(grids, x: int, z: int, include_raw: bool) -> list[dict[str, Any]]:
        # The terrain decoder owns per-record layout and material vocabulary.
        surfaces = [grid.tile(x, z) for grid in grids if 0 <= x < grid.width and 0 <= z < grid.height]
        if not include_raw:
            for surface in surfaces:
                surface.pop("raw_record_hex", None)
        return surfaces

    def window(self, zone_id: int, x: int, y: int, z: int, radius: int, *, include_raw: bool = False) -> dict[str, Any]:
        if not 0 <= radius <= 16:
            raise ValueError("radius must be between 0 and 16")
        tiles = [
            self.tile(zone_id, tx, y, tz, include_raw=include_raw)
            for tz in range(z - radius, z + radius + 1)
            for tx in range(x - radius, x + radius + 1)
        ]
        return {
            "format": "black2-ai-map-window/v1", "coordinate_space": GRID_SPACE,
            "zone_id": zone_id, "y": y,
            "bounds": {"min_x": x - radius, "max_x": x + radius, "min_z": z - radius, "max_z": z + radius},
            "width": 2 * radius + 1, "height": 2 * radius + 1,
            "order": "row-major, x increases east, z increases south",
            "tiles": tiles,
        }
