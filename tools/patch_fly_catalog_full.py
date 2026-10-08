code = """\"\"\"Fast travel and Fly (Move 19) mechanics evaluator for Pokémon Black 2.

Extracts official town landing targets directly from ROM ZoneHeader fly_x/fly_y/fly_z,
evaluates outdoor flight permission (enable_fly_from), and resolves fast-travel
landing doorsteps.
\"\"\"
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from .location_catalog import RomLocationCatalog
from .player_capabilities import MOVE_FLY

CANONICAL_EN_NAMES: dict[int, str] = {
    0: "Black City", 6: "Striaton City", 16: "Nacrene City", 28: "Castelia City",
    40: "Castelia City", 62: "Nimbasa City", 96: "Driftveil City", 107: "Mistralton City",
    113: "Icirrus City", 120: "Opelucid City", 136: "Pokémon League", 147: "Unity Tower",
    154: "Pinwheel Forest", 157: "Desert Resort", 191: "PWT (Pokémon World Tournament)",
    194: "Chargestone Cave", 198: "Twist Mountain", 205: "Dragonspiral Tower",
    230: "Giant Chasm", 235: "Liberty Garden", 238: "P2 Laboratory", 240: "Undella Bay",
    317: "Route 1", 319: "Route 2", 321: "Route 3", 326: "Route 4", 329: "Route 5",
    331: "Route 6", 337: "Route 7", 345: "Route 8", 348: "Route 9", 355: "Route 10",
    365: "Route 11", 368: "Route 12", 370: "Route 13", 374: "Route 14", 378: "Route 15",
    383: "Route 16", 387: "Route 18", 389: "Nuvema Town", 397: "Accumula Town",
    406: "Lacunosa Town", 412: "Undella Town", 423: "Route 17", 427: "Aspertia City",
    437: "Route 19", 439: "Floccesy Town", 444: "Floccesy Ranch", 448: "Virbank City",
    456: "Virbank Complex", 458: "Lentimas Town", 461: "Reversal Mountain",
    463: "Route 21", 465: "Humilau City", 474: "Route 22", 475: "Route 23", 573: "Victory Road"
}

EXTERIOR_GYM_MAP: dict[int, int] = {
    427: 1, 489: 1,
    448: 2, 502: 2,
    28: 3, 40: 3, 488: 3,
    62: 4, 63: 4,
    96: 5, 97: 5,
    107: 6, 108: 6,
    120: 7, 121: 7,
    465: 8, 473: 8,
}

POKECENTER_ZONES: set[int] = {
    0, 6, 16, 28, 40, 62, 96, 107, 113, 120, 136, 191,
    389, 397, 406, 412, 427, 439, 448, 458, 465, 573
}


@dataclass(frozen=True)
class FlyDestination:
    zone_id: int
    name_zh: str
    environment: str
    landing_grid: Dict[str, int]
    landing_world: Dict[str, int]
    enable_fly_from: bool

    def as_dict(self) -> dict[str, Any]:
        gym_idx = EXTERIOR_GYM_MAP.get(self.zone_id)
        has_gym = gym_idx is not None
        gym_info = None
        if has_gym:
            from .gym_catalog import GYM_DEFINITIONS
            g_def = next((g for g in GYM_DEFINITIONS if g["gym_index"] == gym_idx), None)
            if g_def:
                badge_obtained = False
                try:
                    from ..progression.state import progression_state_service
                    prog = progression_state_service.latest or {}
                    b_mask = int(prog.get("badges", {}).get("mask", 0) or 0)
                    badge_obtained = bool(b_mask & (1 << g_def["badge_index"]))
                except Exception:
                    pass
                gym_info = {
                    "gym_index": gym_idx,
                    "gym_name_zh": g_def.get("gym_name_zh"),
                    "leader_name_zh": g_def.get("leader_name_zh"),
                    "type_specialty": g_def.get("type_specialty"),
                    "type_specialty_zh": g_def.get("type_specialty_zh"),
                    "badge_name_zh": g_def.get("badge_name_zh"),
                    "badge_obtained": badge_obtained,
                }

        if has_gym:
            category = "gym_city"
            category_zh = "道馆重镇"
        elif self.name_zh.endswith("市"):
            category = "major_city"
            category_zh = "主要城市"
        elif self.name_zh.endswith("镇") or "牧场" in self.name_zh:
            category = "town"
            category_zh = "特色城镇"
        elif "公路" in self.name_zh or "航道" in self.name_zh:
            category = "route"
            category_zh = "野外公路"
        else:
            category = "landmark"
            category_zh = "战略地标"

        has_pc = self.zone_id in POKECENTER_ZONES
        en_name = CANONICAL_EN_NAMES.get(self.zone_id, f"Zone {self.zone_id}")

        return {
            "zone_id": self.zone_id,
            "name": self.name_zh,
            "name_zh": self.name_zh,
            "name_en": en_name,
            "category": category,
            "category_zh": category_zh,
            "environment": self.environment,
            "has_pokemon_center": has_pc,
            "has_gym": has_gym,
            "gym_info": gym_info,
            "landing_grid": self.landing_grid,
            "landing_world": self.landing_world,
            "enable_fly_from": self.enable_fly_from,
            "landing_description": f"{self.name_zh}宝可梦中心门外门垫 (Pokemon Center Doorstep)" if has_pc else f"{self.name_zh}官方降落点 (Landing Point)",
            "fly_shortcut_payload": {"destination": self.zone_id},
        }


class FastTravelService:
    \"\"\"Manages Fly destinations, ZoneHeader flight gates, and fast-travel actions.\"\"\"

    def __init__(self, provider: Any = None) -> None:
        self.provider = provider
        self._destinations_cache: Optional[List[FlyDestination]] = None

    def _resolve_provider(self) -> Any:
        if self.provider is not None:
            return self.provider
        from .static_navigation import RomStaticNavigationGraph
        self.provider = RomStaticNavigationGraph()
        return self.provider

    def find_destination(self, query: int | str) -> dict[str, Any] | None:
        \"\"\"Find a destination by zone_id (int) or name substring (str).\"\"\"
        dests = self.get_destinations()
        if isinstance(query, int):
            return next((d for d in dests if int(d["zone_id"]) == query), None)
        q_str = str(query).strip().lower()
        if q_str.isdigit():
            zid = int(q_str)
            return next((d for d in dests if int(d["zone_id"]) == zid), None)
        for d in dests:
            name_zh = str(d.get("name_zh") or d.get("name", "")).lower()
            name_en = str(d.get("name_en", "")).lower()
            if q_str in name_zh or name_zh in q_str or q_str in name_en or name_en in q_str:
                return d
        return None

    def get_destinations(
        self,
        category: str | None = None,
        search: str | None = None,
        has_pokemon_center: bool | None = None,
        has_gym: bool | None = None,
    ) -> list[dict[str, Any]]:
        if self._destinations_cache is None:
            provider = self._resolve_provider()
            rom = provider.rom
            loc_catalog = RomLocationCatalog(rom)
            dests: List[FlyDestination] = []

            seen_coords: Set[tuple[int, int]] = set()

            for zid in range(rom.zone_count):
                try:
                    z = rom.zone(zid)
                    if z.enable_fly_from and (z.fly_x != 0 or z.fly_z != 0):
                        gx = z.fly_x // 16
                        gy = z.fly_y // 16
                        gz = z.fly_z // 16
                        coords = (gx, gz)
                        if coords in seen_coords:
                            continue
                        seen_coords.add(coords)

                        label = loc_catalog.zone_label(zid)
                        dests.append(FlyDestination(
                            zone_id=zid,
                            name_zh=label.name_zh or f"Zone {zid}",
                            environment=label.environment,
                            landing_grid={"x": gx, "y": gy, "z": gz},
                            landing_world={"x": z.fly_x, "y": z.fly_y, "z": z.fly_z},
                            enable_fly_from=bool(z.enable_fly_from),
                        ))
                except Exception:
                    continue

            dests.sort(key=lambda d: d.zone_id)
            self._destinations_cache = dests

        rows = [d.as_dict() for d in self._destinations_cache]

        # Apply optional filters
        if category:
            cat_norm = category.lower().rstrip("s")
            cat_alias = {
                "gym_citie": "gym_city", "gym_city": "gym_city", "gym": "gym_city",
                "citie": "major_city", "city": "major_city", "major_city": "major_city",
                "town": "town",
                "landmark": "landmark",
                "route": "route",
            }
            target_cat = cat_alias.get(cat_norm, cat_norm)
            if target_cat != "all":
                rows = [r for r in rows if r.get("category") == target_cat]

        if search:
            q = search.lower().strip()
            rows = [
                r for r in rows
                if q in str(r.get("zone_id"))
                or q in str(r.get("name_zh", "")).lower()
                or q in str(r.get("name_en", "")).lower()
            ]

        if has_pokemon_center is not None:
            rows = [r for r in rows if r.get("has_pokemon_center") is has_pokemon_center]

        if has_gym is not None:
            rows = [r for r in rows if r.get("has_gym") is has_gym]

        return rows

    def get_flyable_regions_catalog(
        self,
        category: str | None = None,
        search: str | None = None,
        has_pokemon_center: bool | None = None,
        has_gym: bool | None = None,
        current_zone_id: int | None = None,
        current_grid: dict[str, Any] | None = None,
        party_moves: Set[int] | None = None,
        flying_mount: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        all_rows = self.get_destinations()
        filtered_rows = self.get_destinations(
            category=category,
            search=search,
            has_pokemon_center=has_pokemon_center,
            has_gym=has_gym,
        )

        cat_counts: dict[str, int] = {}
        for r in all_rows:
            c = r.get("category", "other")
            cat_counts[c] = cat_counts.get(c, 0) + 1

        eval_res = self.evaluate_fly(current_zone_id or 0, party_moves)

        curr_name = "未定"
        if current_zone_id is not None:
            cur_dest = next((d for d in all_rows if d["zone_id"] == current_zone_id), None)
            if cur_dest:
                curr_name = cur_dest["name_zh"]
            else:
                try:
                    provider = self._resolve_provider()
                    lbl = RomLocationCatalog(provider.rom).zone_label(current_zone_id)
                    curr_name = lbl.name_zh
                except Exception:
                    curr_name = f"Zone {current_zone_id}"

        jet_badge = False
        try:
            from ..progression.state import progression_state_service
            prog = progression_state_service.latest or {}
            b_mask = int(prog.get("badges", {}).get("mask", 0) or 0)
            jet_badge = bool(b_mask & (1 << 5)) # Jet Badge is badge index 5
        except Exception:
            pass

        return {
            "format": "black2-flyable-regions/v1",
            "status": "ready",
            "total_destinations": len(all_rows),
            "filtered_destinations_count": len(filtered_rows),
            "current_flight_status": {
                "can_fly_now": eval_res.get("legal", False),
                "current_zone_id": current_zone_id,
                "current_zone_name": curr_name,
                "current_position": current_grid or {},
                "flying_mount": flying_mount,
                "jet_badge_obtained": jet_badge,
                "reason": eval_res.get("reason"),
            },
            "categories_summary": {
                "gym_cities": cat_counts.get("gym_city", 0),
                "major_cities": cat_counts.get("major_city", 0),
                "towns": cat_counts.get("town", 0),
                "landmarks": cat_counts.get("landmark", 0),
                "routes": cat_counts.get("route", 0),
            },
            "destinations": filtered_rows,
        }

    def can_fly_from_zone(self, zone_id: int) -> bool:
        try:
            provider = self._resolve_provider()
            z = provider.rom.zone(int(zone_id))
            return bool(z.enable_fly_from)
        except Exception:
            return False

    def evaluate_fly(self, current_zone_id: int, party_moves: Set[int] | None = None) -> dict[str, Any]:
        moves = party_moves or set()
        has_fly = (MOVE_FLY in moves)
        can_fly_zone = self.can_fly_from_zone(current_zone_id)

        legal = has_fly and can_fly_zone
        if not has_fly:
            reason = "队伍中没有任何宝可梦学会技能「飞翔 (Fly)」(Move ID 19)"
        elif not can_fly_zone:
            reason = "当前区域属于室内或洞穴，受 ZoneHeader 规则约束禁止飞翔"
        else:
            reason = "满足飞翔条件：室外大地图，已掌握飞翔技能，可快速前往合众各城镇"

        return {
            "legal": legal,
            "has_move_fly": has_fly,
            "current_zone_allows_fly": can_fly_zone,
            "reason": reason,
            "available_destinations_count": len(self.get_destinations()) if legal else 0,
        }


fast_travel_service = FastTravelService()
"""
with open("backend/black2/world/fast_travel.py", "w", encoding="utf-8") as f:
    f.write(code)
print("Updated backend/black2/world/fast_travel.py with rich fly catalog!")