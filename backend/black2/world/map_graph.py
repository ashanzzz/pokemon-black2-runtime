"""ROM-wide map graph for scene transitions and future pathfinding.

The graph deliberately keeps ROM warp fields lossless.  A warp target is a
candidate Zone only when its raw value falls inside the decoded ZoneData table;
it is promoted to an observed transition only by a separate runtime evidence
source.  This prevents a coincidental raw ID from becoming navigation truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .location_catalog import RomLocationCatalog
from .gen5_rom_map import Gen5RomMap


# These are operator-confirmed labels from the current debugging session.  The
# raw ROM location_name_id remains in every node and can replace these labels
# when a text-table decoder is added.
ZONE_LABEL_OVERRIDES: dict[int, dict[str, str]] = {
    439: {"name_zh": "算木镇（室外）", "name_en": "Floccesy Town (Exterior)"},
    443: {"name_zh": "算木镇 精灵中心（室内）", "name_en": "Floccesy Town Pokémon Center (Interior)"},
}


def _environment(map_type: int, is_exterior: bool) -> str:
    if is_exterior:
        return "exterior"
    if map_type in {0x10, 0x11, 0x12, 0x13}:
        return "interior"
    return "interior"


@dataclass
class RomMapGraphService:
    rom: Gen5RomMap
    _cache: dict[str, Any] | None = None

    def _label(self, zone_id: int, location_name_id: int, parent_zone_id: int, environment: str) -> dict[str, Any]:
        override = ZONE_LABEL_OVERRIDES.get(zone_id)
        catalog = RomLocationCatalog(self.rom)
        try:
            rom_label = catalog.zone_label(zone_id)
            if override:
                return {
                    **rom_label.as_dict(),
                    **override,
                    "source": "operator_confirmed+rom_text",
                    "confidence": "verified_rom_text",
                }
            return {
                **rom_label.as_dict(),
                "name_en": f"Zone {zone_id} ({rom_label.environment})",
                "location_name_id": location_name_id,
                "parent_zone_id": parent_zone_id,
            }
        except Exception:
            return {
                "name_zh": f"Zone {zone_id}（{environment}）",
                "name_en": f"Zone {zone_id} ({environment})",
                "source": "zone_id_fallback",
                "confidence": "unresolved_name",
                "location_name_id": location_name_id,
                "parent_zone_id": parent_zone_id,
            }

    def _node(self, zone_id: int) -> dict[str, Any]:
        zone = self.rom.zone(zone_id)
        area = self.rom.area(zone.area_id)
        env = _environment(zone.map_type, area.is_exterior)
        label = self._label(zone_id, zone.location_name_id, zone.parent_zone_id, env)
        return {
            "node_id": f"zone:{zone_id}",
            "zone_id": zone_id,
            "parent_zone_id": zone.parent_zone_id,
            "area_id": zone.area_id,
            "matrix_id": zone.matrix_id,
            "entities_id": zone.entities_id,
            "map_type": zone.map_type,
            "environment": env,
            "is_exterior": area.is_exterior,
            "location_name_id": zone.location_name_id,
            "location_name_display_type": zone.location_name_display_type,
            "label": label,
            "source": f"rom:/a/0/1/2[{zone_id}] + rom:/a/0/1/3[{zone.area_id}]",
        }

    def build(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        # Decode all nodes first so an edge can always attach the destination
        # label, including when the destination Zone has a larger numeric ID.
        zone_entities: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for zone_id in range(self.rom.zone_count):
            try:
                node = self._node(zone_id)
                nodes[str(zone_id)] = node
                entities = self.rom.entities(node["entities_id"])
            except Exception:
                # A malformed archive member must not make the ROM-wide graph
                # endpoint unusable.  Keep the valid Zones and expose their
                # edges; the source-specific detail remains available through
                # the existing scene/zone endpoint.
                continue
            zone_entities.append((node, entities))

        for node, entities in zone_entities:
            zone_id = int(node["zone_id"])
            for warp in entities.get("warps") or []:
                try:
                    raw_target = warp.get("target_zone_or_map_raw")
                    target_zone = raw_target if isinstance(raw_target, int) and 0 <= raw_target < self.rom.zone_count else None
                    target_node = nodes.get(str(target_zone)) if target_zone is not None else None
                    # Warp +0x08/+0x0C are horizontal candidates.  +0x12 is
                    # an unverified raw parameter, never a 3-D elevation.
                    source_world = {
                        "x": float(warp.get("x_raw", 0)),
                        "y": None,
                        "z": float(warp.get("y_raw", 0)),
                    }
                    edge = {
                    "edge_id": f"zone:{zone_id}:warp:{warp.get('id', 0)}",
                    "kind": "warp",
                    "source_zone_id": zone_id,
                    "source_node_id": f"zone:{zone_id}",
                    "source_warp_id": warp.get("id"),
                    "source_position": source_world,
                    "source_tile_candidate": {
                        "x": (float(warp["x_raw"]) / 16.0) if isinstance(warp.get("x_raw"), int) else None,
                        "z": (float(warp["y_raw"]) / 16.0) if isinstance(warp.get("y_raw"), int) else None,
                        "x_extent_raw": warp.get("x_extent_raw"),
                        "y_extent_raw": warp.get("y_extent_raw"),
                    },
                    "target_zone_or_map_raw": raw_target,
                    "target_warp_id": None,
                    "target_warp_arg2_raw": warp.get("arg2_raw"),
                    "target_warp_semantics_verified": False,
                    "target_zone_id_candidate": target_zone,
                    "target_node_id_candidate": f"zone:{target_zone}" if target_zone is not None else None,
                    "target_label_candidate": (target_node or {}).get("label"),
                    "destination_semantics": "rom_zone_candidate_only" if target_zone is not None else "raw_unresolved",
                    "verification": {
                        "status": "rom_candidate" if target_zone is not None else "unresolved",
                        "source": "rom:/a/1/2/6",
                        "runtime_observed": False,
                    },
                    "source_record": warp,
                    }
                    edges.append(edge)
                except Exception:
                    continue

        self._cache = {
            "format": "black2-rom-navigation-graph/v1",
            "coordinate_space": "gen5-field-world-v1",
            "semantic_policy": {
                "zone_names": "Chinese label first when a confirmed/registered label exists; raw location_name_id is retained",
                "warp_targets": "target_zone_or_map_raw is lossless; arg2 target-record and landing are unresolved",
                "runtime_promotion": "set verification.runtime_observed only after a PlayerRuntime Zone transition confirms the edge",
                "parent_zone": "ROM parent_zone_id is preserved as an ownership candidate for interior maps",
            },
            "summary": {
                "zone_count": len(nodes),
                "warp_edge_count": len(edges),
                "candidate_target_count": sum(edge["target_zone_id_candidate"] is not None for edge in edges),
                "unresolved_target_count": sum(edge["target_zone_id_candidate"] is None for edge in edges),
            },
            "nodes": list(nodes.values()),
            "edges": edges,
        }
        return self._cache

    def zone(self, zone_id: int) -> dict[str, Any]:
        graph = self.build()
        for node in graph.get("nodes") or []:
            if isinstance(node, dict) and node.get("zone_id") == zone_id:
                return node
        raise IndexError(f"Zone {zone_id} is not present in the navigation graph")


def build_rom_map_graph(rom: Gen5RomMap) -> dict[str, Any]:
    return RomMapGraphService(rom).build()
