"""General staircase corridor clustering and transition layer flattening for Pokémon Black 2.

Discovers, clusters, and abstracts multi-step staircases (N steps: 2-step, 3-step,
20-step towers) into unified, single-layer transition corridors connecting upper
and lower floors.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class StairStep:
    step_index: int  # 1-based (1 .. N)
    x: int
    z: int
    world_y: float
    slope_index: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step_index,
            "x": self.x,
            "z": self.z,
            "world_y": round(self.world_y, 2),
            "slope_index": self.slope_index,
            "role": "lower_step" if self.step_index == 1 else "upper_step",
        }


@dataclass(frozen=True)
class StaircaseCorridor:
    corridor_id: str
    zone_id: int
    total_steps: int
    steps: Tuple[StairStep, ...]
    lower_portal: Optional[Dict[str, Any]]
    upper_portal: Optional[Dict[str, Any]]
    axis: str  # "east_west" or "north_south"
    rising_direction: str  # "West", "East", "North", "South"
    flattened_slice_y: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "corridor_id": self.corridor_id,
            "zone_id": self.zone_id,
            "total_steps": self.total_steps,
            "axis": self.axis,
            "rising_direction": self.rising_direction,
            "flattened_slice_y": self.flattened_slice_y,
            "steps": [s.as_dict() for s in self.steps],
            "lower_portal": self.lower_portal,
            "upper_portal": self.upper_portal,
            "handrails": "north_south_blocked" if self.axis == "east_west" else "east_west_blocked",
        }


class StaircaseCorridorService:
    """Extracts, clusters, and flattens all staircase networks in a Zone."""

    def __init__(self, provider: Any = None) -> None:
        self.provider = provider
        self._cache: Dict[int, List[StaircaseCorridor]] = {}

    def _resolve_provider(self) -> Any:
        if self.provider is not None:
            return self.provider
        from .static_navigation import RomStaticNavigationGraph
        self.provider = RomStaticNavigationGraph()
        return self.provider

    def analyze_zone(self, zone_id: int) -> List[StaircaseCorridor]:
        zone_id = int(zone_id)
        if zone_id in self._cache:
            return self._cache[zone_id]

        provider = self._resolve_provider()
        surfaces = provider._zone_surfaces(zone_id) if hasattr(provider, '_zone_surfaces') else provider._decode_zone_surfaces(zone_id)
        
        slope_tiles: Dict[Tuple[int, int], Dict[str, Any]] = {}
        by_coord: Dict[Tuple[int, int], List[dict[str, Any]]] = {}

        for s in surfaces:
            x, z = s["x"], s["z"]
            surf = s["surface"]
            by_coord.setdefault((x, z), []).append(surf)
            h = surf.get("height", {})
            sl = h.get("slope_index", 0)
            if sl > 0:
                ry = h.get("chunk_relative_world_y") or 0.0
                slope_tiles[(x, z)] = {"slope_index": sl, "world_y": ry, "surface": surf}

        visited: Set[Tuple[int, int]] = set()
        corridors: List[StaircaseCorridor] = []

        for (x, z), info in sorted(slope_tiles.items()):
            if (x, z) in visited:
                continue

            # Breadth-first clustering of adjacent slope tiles
            cluster: List[Tuple[int, int]] = [(x, z)]
            visited.add((x, z))
            queue = [(x, z)]
            while queue:
                cx, cz = queue.pop(0)
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, nz = cx + dx, cz + dz
                    if (nx, nz) in slope_tiles and (nx, nz) not in visited:
                        visited.add((nx, nz))
                        cluster.append((nx, nz))
                        queue.append((nx, nz))

            # Monotonically sort steps from lowest elevation to highest
            cluster.sort(key=lambda coord: slope_tiles[coord]["world_y"])
            total_steps = len(cluster)
            if total_steps == 0:
                continue

            steps_tuple: List[StairStep] = []
            for idx, (cx, cz) in enumerate(cluster):
                c_info = slope_tiles[(cx, cz)]
                steps_tuple.append(StairStep(
                    step_index=idx + 1,
                    x=cx,
                    z=cz,
                    world_y=c_info["world_y"],
                    slope_index=c_info["slope_index"],
                ))

            # Infer axis & rising direction
            first_x, first_z = cluster[0]
            last_x, last_z = cluster[-1]
            dx = last_x - first_x
            dz = last_z - first_z

            if abs(dx) >= abs(dz):
                axis = "east_west"
                rising_dir = "East" if dx > 0 else "West" if dx < 0 else "West"
            else:
                axis = "north_south"
                rising_dir = "South" if dz > 0 else "North" if dz < 0 else "North"

            # Detect lower portal (adjacent flat tile next to step 1)
            lower_portal = None
            step1_x, step1_z = cluster[0]
            step1_y = slope_tiles[cluster[0]]["world_y"]
            for pdx, pdz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                px, pz = step1_x + pdx, step1_z + pdz
                if (px, pz) not in slope_tiles:
                    p_surfs = by_coord.get((px, pz), [])
                    for ps in p_surfs:
                        ph = ps.get("height", {})
                        blocked = bool(ps.get("collision", {}).get("static_blocked") or ps.get("static_blocked"))
                        if ph.get("slope_index", 0) == 0 and not blocked:
                            py = ph.get("chunk_relative_world_y") or 0.0
                            if py <= step1_y:
                                lower_portal = {"x": px, "z": pz, "world_y": py, "floor_y": int(round(py / 16.0))}
                                break
                    if lower_portal:
                        break

            # Detect upper portal (adjacent flat tile next to step N)
            upper_portal = None
            stepN_x, stepN_z = cluster[-1]
            stepN_y = slope_tiles[cluster[-1]]["world_y"]
            for pdx, pdz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                px, pz = stepN_x + pdx, stepN_z + pdz
                if (px, pz) not in slope_tiles:
                    p_surfs = by_coord.get((px, pz), [])
                    for ps in p_surfs:
                        ph = ps.get("height", {})
                        blocked = bool(ps.get("collision", {}).get("static_blocked") or ps.get("static_blocked"))
                        if ph.get("slope_index", 0) == 0 and not blocked:
                            py = ph.get("chunk_relative_world_y") or 0.0
                            if py >= stepN_y:
                                upper_portal = {"x": px, "z": pz, "world_y": py, "floor_y": int(round(py / 16.0))}
                                break
                    if upper_portal:
                        break

            # Compute flattened slice y (intermediate layer)
            lower_fy = lower_portal["floor_y"] if lower_portal else 0
            upper_fy = upper_portal["floor_y"] if upper_portal else 2
            flattened_slice = int(round((lower_fy + upper_fy) / 2.0))
            if flattened_slice == lower_fy:
                flattened_slice = lower_fy + 1

            cid = f"stair:{zone_id}:{first_x}_{last_x}_{first_z}"
            corridors.append(StaircaseCorridor(
                corridor_id=cid,
                zone_id=zone_id,
                total_steps=total_steps,
                steps=tuple(steps_tuple),
                lower_portal=lower_portal,
                upper_portal=upper_portal,
                axis=axis,
                rising_direction=rising_dir,
                flattened_slice_y=flattened_slice,
            ))

        self._cache[zone_id] = corridors
        return corridors

    def get_corridor_at(self, zone_id: int, x: int, z: int) -> Optional[StaircaseCorridor]:
        corridors = self.analyze_zone(zone_id)
        for c in corridors:
            for s in c.steps:
                if s.x == x and s.z == z:
                    return c
        return None

    def evaluate_player_step(self, zone_id: int, px: int, pz: int, live_world_y: float) -> Optional[dict[str, Any]]:
        c = self.get_corridor_at(zone_id, px, pz)
        if not c:
            return None

        # Find exact step matching coordinates
        step_obj = next((s for s in c.steps if s.x == px and s.z == pz), c.steps[0])
        return {
            "in_staircase_corridor": True,
            "corridor_id": c.corridor_id,
            "current_step": step_obj.step_index,
            "total_steps": c.total_steps,
            "step_world_y": round(step_obj.world_y, 2),
            "slope_index": step_obj.slope_index,
            "flattened_slice_y": c.flattened_slice_y,
            "axis": c.axis,
            "rising_direction": c.rising_direction,
            "lower_portal": c.lower_portal,
            "upper_portal": c.upper_portal,
            "ai_guidance": f"角色正处于第 {step_obj.step_index} 阶踏面 (共 {c.total_steps} 阶 · 标高 {step_obj.world_y:.1f})；"
                          f"沿 {c.rising_direction} 上行通往上层，反向通往下层；护栏封死，禁止侧移。",
        }


staircase_corridor_service = StaircaseCorridorService()
