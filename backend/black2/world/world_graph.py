"""World-level macro graph and cross-zone router for Pokémon Black 2.

Combines:
1. 1089 ROM Warp edges (doors, gates, caves, interiors)
2. Seamless Matrix chunk boundary seams
3. Regional ferries and cargo flight transports
4. Progression StoryGate gates attached to boundary transitions
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import heapq
from typing import Any, Dict, List, Optional, Tuple

from .gen5_rom_map import Gen5RomMap, MAP_MATRIX_PATH
from .map_graph import RomMapGraphService
from ..progression.story_gate import UNOVA_STORY_GATES, StoryGateDefinition
# Matrix seams that are blocked by solid cliffs/gate buildings in the actual game geometry
BLOCKED_MATRIX_SEAMS: set[tuple[int, int]] = {
    (448, 446), (446, 448),  # Virbank City <-> Route 20: separated by Virbank Gate (Zone 447)
    (456, 446), (446, 456),  # Virbank Complex Exterior <-> Route 20: separated by cliffs and ocean
    (437, 427), (427, 437),  # Route 19 <-> Aspertia City: separated by Aspertia Gate (Zone 438)
}


@dataclass(frozen=True)
class WorldRouteStep:
    step_index: int
    from_zone: int
    to_zone: int
    kind: str
    from_name_zh: str
    from_name_en: str
    to_name_zh: str
    to_name_en: str
    story_gate_id: Optional[str] = None
    story_gate_unlocked: bool = True
    blocking_reason_zh: Optional[str] = None
    blocking_reason_en: Optional[str] = None
    connector_metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "from_zone": self.from_zone,
            "to_zone": self.to_zone,
            "kind": self.kind,
            "from_name_zh": self.from_name_zh,
            "from_name_en": self.from_name_en,
            "to_name_zh": self.to_name_zh,
            "to_name_en": self.to_name_en,
            "story_gate_id": self.story_gate_id,
            "story_gate_unlocked": self.story_gate_unlocked,
            "blocking_reason_zh": self.blocking_reason_zh,
            "blocking_reason_en": self.blocking_reason_en,
            "connector_metadata": self.connector_metadata,
        }


class WorldGraph:
    """Directed macro graph of all 615 Unova zones with story-gate awareness."""

    def __init__(self, rom: Gen5RomMap | None = None) -> None:
        self.rom = rom or Gen5RomMap()
        self._graph_service = RomMapGraphService(self.rom)
        self._adj: dict[int, list[tuple[int, str, dict[str, Any]]]] = {}
        self._zone_meta: dict[int, dict[str, Any]] = {}
        self._gate_map: dict[tuple[int, int], StoryGateDefinition] = {}
        self._built = False

    def build(self) -> None:
        if self._built:
            return

        # 1. Load StoryGates by (source_zone, destination_zone)
        for g in UNOVA_STORY_GATES:
            self._gate_map[(g.source_zone, g.destination_zone)] = g

        # 2. Decode all ROM zones and warps
        graph_data = self._graph_service.build()
        nodes = graph_data.get("nodes", {})
        edges = graph_data.get("edges", [])

        node_iterable = nodes.values() if isinstance(nodes, dict) else nodes
        for node in node_iterable:
            zid = int(node.get("zone_id", 0))
            label = node.get("label", {})
            self._zone_meta[zid] = {
                "zone_id": zid,
                "name_zh": label.get("name_zh") or f"Zone {zid}",
                "name_en": label.get("name_en") or f"Zone {zid}",
                "environment": node.get("environment", "unknown"),
                "matrix_id": node.get("matrix_id"),
            }

        # 3. Add Warp edges
        for e in edges:
            src = e.get("source_zone_id")
            dst = e.get("target_zone_id_candidate")
            if src is not None and dst is not None and src != dst:
                self._adj.setdefault(src, []).append((dst, "warp", e))

        # 4. Add Matrix seamless chunk boundary seams
        matrix_files = self.rom.archive(MAP_MATRIX_PATH).files
        for m_idx in range(len(matrix_files)):
            try:
                mat = self.rom.matrix(m_idx)
                grid: dict[tuple[int, int], int] = {}
                for z in range(mat.height):
                    for x in range(mat.width):
                        c = mat.cell(x, z)
                        zid = c.get("zone_id")
                        if zid is not None and zid != 4294967295 and zid != 65535:
                            grid[(x, z)] = zid
                for (x, z), zid in grid.items():
                    for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        neighbor_pos = (x + dx, z + dz)
                        if neighbor_pos in grid:
                            nzid = grid[neighbor_pos]
                            if nzid != zid:
                                if (zid, nzid) not in BLOCKED_MATRIX_SEAMS and (nzid, zid) not in BLOCKED_MATRIX_SEAMS:
                                    self._adj.setdefault(zid, []).append((nzid, "matrix_seam", {"matrix_id": m_idx}))
            except Exception:
                pass

        # 5. Add regional key transports (Ferries, Planes)
        self._adj.setdefault(448, []).append((378, "ferry", {"name": "Virbank-Castelia Ferry"}))
        self._adj.setdefault(378, []).append((448, "ferry", {"name": "Castelia-Virbank Ferry"}))
        self._adj.setdefault(453, []).append((378, "ferry", {"name": "Virbank Port-Castelia Ferry"}))

        self._adj.setdefault(107, []).append((518, "plane", {"name": "Mistralton-Lentimas Cargo Plane"}))
        self._adj.setdefault(518, []).append((107, "plane", {"name": "Lentimas-Mistralton Cargo Plane"}))

        self._built = True

    def get_zone_name(self, zone_id: int) -> tuple[str, str]:
        meta = self._zone_meta.get(zone_id, {})
        return meta.get("name_zh", f"Zone {zone_id}"), meta.get("name_en", f"Zone {zone_id}")

    def _is_gate_unlocked(self, gate: StoryGateDefinition, badge_mask: int, badge_count: int) -> tuple[bool, list[int]]:
        missing = []
        for b_id in gate.required_badges:
            if not bool(badge_mask & (1 << (b_id - 1))):
                missing.append(b_id)
        count_ok = badge_count >= gate.required_badge_count
        return bool(not missing and count_ok), missing

    def find_route(
        self,
        start_zone: int,
        goal_zone: int,
        *,
        badge_mask: int = 0,
        badge_count: int = 0,
    ) -> dict[str, Any]:
        """Find a macro-level route across Unova zones with StoryGate evaluation."""
        self.build()

        start_name_zh, start_name_en = self.get_zone_name(start_zone)
        goal_name_zh, goal_name_en = self.get_zone_name(goal_zone)

        if start_zone == goal_zone:
            return {
                "format": "black2-world-route/v1",
                "status": "already_at_destination",
                "traversable": True,
                "start_zone": start_zone,
                "goal_zone": goal_zone,
                "start_name_zh": start_name_zh,
                "start_name_en": start_name_en,
                "goal_name_zh": goal_name_zh,
                "goal_name_en": goal_name_en,
                "zone_path": [start_zone],
                "steps": [],
                "step_count": 0,
                "blocked_by_gate": None,
            }

        def run_dijkstra(allow_locked: bool):
            queue: list[tuple[float, int, list[int], list[tuple[int, str, dict[str, Any]]]]] = [
                (0.0, start_zone, [start_zone], [])
            ]
            visited: dict[int, float] = {start_zone: 0.0}
            while queue:
                cost, curr, path, edge_path = heapq.heappop(queue)
                if curr == goal_zone:
                    return path, edge_path
                if cost > visited.get(curr, 1e9):
                    continue
                for neighbor, kind, edge_meta in self._adj.get(curr, []):
                    gate = self._gate_map.get((curr, neighbor))
                    base_cost = 1.0 if kind in ("matrix_seam", "warp") else 2.0
                    gate_unlocked = True
                    if gate is not None:
                        unlocked, _ = self._is_gate_unlocked(gate, badge_mask, badge_count)
                        gate_unlocked = unlocked
                    if not gate_unlocked:
                        if not allow_locked:
                            continue
                        base_cost += 1000.0

                    new_cost = cost + base_cost
                    if neighbor not in visited or new_cost < visited[neighbor]:
                        visited[neighbor] = new_cost
                        heapq.heappush(queue, (new_cost, neighbor, path + [neighbor], edge_path + [(neighbor, kind, edge_meta)]))
            return None, None

        path, edges = run_dijkstra(allow_locked=False)
        is_strictly_traversable = path is not None

        if not is_strictly_traversable:
            path, edges = run_dijkstra(allow_locked=True)

        if path is None or edges is None:
            return {
                "format": "black2-world-route/v1",
                "status": "no_route_found",
                "traversable": False,
                "start_zone": start_zone,
                "goal_zone": goal_zone,
                "start_name_zh": start_name_zh,
                "start_name_en": start_name_en,
                "goal_name_zh": goal_name_zh,
                "goal_name_en": goal_name_en,
                "zone_path": [],
                "steps": [],
                "step_count": 0,
                "blocked_by_gate": None,
            }

        steps = []
        first_blocker = None
        for idx, (to_zone, kind, edge_meta) in enumerate(edges, 1):
            from_zone = path[idx - 1]
            f_zh, f_en = self.get_zone_name(from_zone)
            t_zh, t_en = self.get_zone_name(to_zone)

            gate = self._gate_map.get((from_zone, to_zone))
            gate_id = None
            gate_unlocked = True
            reason_zh = None
            reason_en = None

            if gate is not None:
                gate_id = gate.gate_id
                unlocked, missing = self._is_gate_unlocked(gate, badge_mask, badge_count)
                gate_unlocked = unlocked
                if not gate_unlocked:
                    reason_zh = gate.blocking_reason_zh
                    reason_en = gate.blocking_reason_en
                    if first_blocker is None:
                        first_blocker = {
                            "gate_id": gate.gate_id,
                            "name_zh": gate.name_zh,
                            "name_en": gate.name_en,
                            "at_step": idx,
                            "from_zone": from_zone,
                            "to_zone": to_zone,
                            "required_badges": list(gate.required_badges),
                            "missing_badges": missing,
                            "reason_zh": gate.blocking_reason_zh,
                            "reason_en": gate.blocking_reason_en,
                        }

            steps.append(WorldRouteStep(
                step_index=idx,
                from_zone=from_zone,
                to_zone=to_zone,
                kind=kind,
                from_name_zh=f_zh,
                from_name_en=f_en,
                to_name_zh=t_zh,
                to_name_en=t_en,
                story_gate_id=gate_id,
                story_gate_unlocked=gate_unlocked,
                blocking_reason_zh=reason_zh,
                blocking_reason_en=reason_en,
                connector_metadata=edge_meta,
            ))

        return {
            "format": "black2-world-route/v1",
            "status": "traversable" if is_strictly_traversable else "blocked_by_story_gate",
            "traversable": is_strictly_traversable,
            "start_zone": start_zone,
            "goal_zone": goal_zone,
            "start_name_zh": start_name_zh,
            "start_name_en": start_name_en,
            "goal_name_zh": goal_name_zh,
            "goal_name_en": goal_name_en,
            "zone_path": path,
            "steps": [s.as_dict() for s in steps],
            "step_count": len(steps),
            "blocked_by_gate": first_blocker,
        }


world_graph_service = WorldGraph()
