"""Build complete static original-map descriptors from B2/W2 ROM resources.

The output represents what the ROM defines, not what is currently resident in
RAM.  Dynamic NPC motion, despawns, door animation phase, player state, etc. are
joined later by MapTruthV3.
"""
from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from typing import Any

from .gen5_rom_map import (
    MATRIX_NONE,
    Gen5MapFormatError,
    Gen5RomMap,
    decode_permission_from_chunk,
)


DEFAULT_CHUNK_SPAN_WORLD = 512.0


class OriginalWorldService:
    def __init__(self, rom_path: str | None = None) -> None:
        self.rom = Gen5RomMap(rom_path)

    @lru_cache(maxsize=512)
    def zone(self, zone_id: int) -> dict[str, Any]:
        zone = self.rom.zone(zone_id)
        area = self.rom.area(zone.area_id)
        matrix = self.rom.matrix(zone.matrix_id)
        bundle = self.rom.building_bundle(zone.area_id)
        resource_by_uid = {item.uid: item for item in bundle.resources}

        cells: list[dict[str, Any]] = []
        building_instances: list[dict[str, Any]] = []
        permission_models: list[dict[str, Any]] = []
        missing_building_resources: list[dict[str, Any]] = []

        for cell in matrix.cells():
            chunk_id = int(cell["chunk_id"])
            if chunk_id == MATRIX_NONE:
                cells.append({**cell, "present": False})
                continue
            # A matrix can serve several zones.  For a zone-specific world,
            # preserve every cell but explicitly tag whether it belongs to the
            # selected zone instead of silently deleting neighbouring cells.
            cell_zone = cell.get("zone_id")
            belongs = (cell_zone is None) or (cell_zone == zone_id)
            try:
                chunk = self.rom.chunk(chunk_id)
                permission = decode_permission_from_chunk(chunk)
                if permission is not None:
                    permission_models.append({
                        "chunk_id": chunk_id,
                        "cell": {"x": cell["x"], "y": cell["y"]},
                        "width": permission.width,
                        "height": permission.height,
                        "plane_count": len(permission.planes),
                        "source_file_index": permission.source_file_index,
                        "semantic_status": "raw permission bytes; passability/height meaning unresolved",
                    })
                cell_buildings = []
                for placement in chunk.buildings:
                    resource = resource_by_uid.get(placement.model_uid)
                    item = {
                        "instance_id": f"z{zone_id}-c{cell['x']}_{cell['y']}-b{placement.index}",
                        "chunk_id": chunk_id,
                        "chunk_cell": {"x": cell["x"], "y": cell["y"]},
                        "belongs_to_zone": belongs,
                        "placement_index": placement.index,
                        "model_uid": placement.model_uid,
                        "local_position": {
                            "x": placement.local_x,
                            "y": placement.local_y,
                            "z": placement.local_z,
                        },
                        # CTRMapV adds the chunk centre to X and subtracts local
                        # Z from the chunk centre.  512 is its fallback span;
                        # MapTruthV3 replaces this with the live Mapper span
                        # when a runtime sample proves it.
                        "world_position_candidate": {
                            "x": (cell["x"] + 0.5) * DEFAULT_CHUNK_SPAN_WORLD + placement.local_x,
                            "y": placement.local_y,
                            "z": (cell["y"] + 0.5) * DEFAULT_CHUNK_SPAN_WORLD - placement.local_z,
                        },
                        "rotation_raw": placement.rotation_raw,
                        "rotation_degrees": placement.rotation_degrees,
                        "resource": None,
                    }
                    if resource is not None:
                        item["resource"] = {
                            "resource_index": resource.resource_index,
                            "uid": resource.uid,
                            "type": resource.type,
                            "door_uid": resource.door_uid,
                            "door_offset": {
                                "x": resource.door_x,
                                "y": resource.door_y,
                                "z": resource.door_z,
                            },
                            "has_door_metadata": resource.has_door_metadata,
                        }
                    else:
                        missing_building_resources.append({
                            "chunk_id": chunk_id,
                            "placement_index": placement.index,
                            "model_uid": placement.model_uid,
                        })
                    building_instances.append(item)
                    cell_buildings.append(item["instance_id"])
                cells.append({
                    **cell,
                    "present": True,
                    "belongs_to_zone": belongs,
                    "terrain_source": f"rom:/a/0/0/8[{chunk_id}]/file0",
                    "building_instance_ids": cell_buildings,
                })
            except (IndexError, Gen5MapFormatError, ValueError) as error:
                cells.append({
                    **cell,
                    "present": True,
                    "belongs_to_zone": belongs,
                    "decode_error": str(error),
                })

        try:
            entities = self.rom.entities(zone.entities_id)
        except (IndexError, Gen5MapFormatError, ValueError) as error:
            entities = {"entities_id": zone.entities_id, "decode_error": str(error)}

        return {
            "format": "black2-original-world/v5",
            "zone_id": zone_id,
            "source_policy": {
                "world_geometry": "ROM static source",
                "current_identity": "not asserted here; use MapTruthV3 + runtime RAM",
                "building_positions": "ROM ChunkBuildings",
                "door_metadata": "ROM AreaBuildingResource",
                "event_coordinates": "raw ROM coordinates; global alignment is not silently guessed",
            },
            "zone": asdict(zone),
            "area": asdict(area),
            "matrix": {
                "matrix_id": matrix.matrix_id,
                "has_zones": matrix.has_zones,
                "width": matrix.width,
                "height": matrix.height,
                "cell_count": matrix.cell_count,
                "trailing_bytes": matrix.trailing_bytes,
            },
            "render_coordinate_system": {
                "chunk_span_world": DEFAULT_CHUNK_SPAN_WORLD,
                "confidence": "candidate_default",
                "reason": "CTRMapV fallback; live FieldG3DMapper.chunkSpan supersedes this when resolved",
            },
            "cells": cells,
            "buildings": building_instances,
            "building_resources": [
                {
                    "resource_index": item.resource_index,
                    "uid": item.uid,
                    "type": item.type,
                    "door_uid": item.door_uid,
                    "door_offset": {"x": item.door_x, "y": item.door_y, "z": item.door_z},
                    "has_door_metadata": item.has_door_metadata,
                    "model_source": (
                        f"rom:/{'a/2/2/5' if area.is_exterior else 'a/2/2/6'}"
                        f"[{area.buildings_id}]/model[{item.resource_index}]"
                    ),
                }
                for item in bundle.resources
            ],
            "permission_models": permission_models,
            "entities": entities,
            "missing_building_resources": missing_building_resources,
            "asset_identity": {
                "terrain_texture_id": area.textures_id,
                "building_bundle_id": area.buildings_id,
                "building_environment": "exterior" if area.is_exterior else "interior",
            },
        }

    def catalog(self) -> dict[str, Any]:
        zones: list[dict[str, Any]] = []
        for zone_id in range(self.rom.zone_count):
            try:
                z = self.rom.zone(zone_id)
                zones.append({
                    "zone_id": z.zone_id,
                    "area_id": z.area_id,
                    "matrix_id": z.matrix_id,
                    "entities_id": z.entities_id,
                    "map_type": z.map_type,
                    "location_name_id": z.location_name_id,
                })
            except Exception as error:
                zones.append({"zone_id": zone_id, "decode_error": str(error)})
        return {
            "format": "black2-original-world-catalog/v5",
            "rom": self.rom.static_identity(),
            "zones": zones,
        }
