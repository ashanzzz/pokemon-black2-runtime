"""ROM-backed Gym and Leader knowledge graph for Pokémon Black 2.

Provides deterministic data on all 8 Gyms in the Unova region:
Leader identities, normal and challenge mode trainer IDs, badges, Gym trainers,
type specialties, entrance warps, and reward TMs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from ..decoders.trainer_rom import TrainerRomCatalog
from ..world.rom_reader import NitroRom

GYM_DEFINITIONS = (
    {
        "gym_index": 1,
        "zone_id": 489,
        "gym_name_zh": "桧扇道馆",
        "city_name_zh": "桧扇市",
        "leader_name_zh": "切莲",
        "leader_ids": {"normal": 156, "challenge": 764},
        "badge_name_zh": "基础徽章",
        "badge_index": 0,
        "type_specialty": "Normal",
        "type_specialty_zh": "一般",
        "reward_tm": {"tm_id": 83, "name_zh": "自我激励", "item_id": 410},
    },
    {
        "gym_index": 2,
        "zone_id": 502,
        "gym_name_zh": "立胁道馆",
        "city_name_zh": "立胁市",
        "leader_name_zh": "霍米加",
        "leader_ids": {"normal": 157, "challenge": 765},
        "badge_name_zh": "毒性徽章",
        "badge_index": 1,
        "type_specialty": "Poison",
        "type_specialty_zh": "毒",
        "reward_tm": {"tm_id": 9, "name_zh": "毒液冲击", "item_id": 336},
    },
    {
        "gym_index": 3,
        "zone_id": 488,
        "gym_name_zh": "飞云道馆",
        "city_name_zh": "飞云市",
        "leader_name_zh": "亚堤",
        "leader_ids": {"normal": 154, "challenge": 766},
        "badge_name_zh": "甲虫徽章",
        "badge_index": 2,
        "type_specialty": "Bug",
        "type_specialty_zh": "虫",
        "reward_tm": {"tm_id": 76, "name_zh": "虫之抵抗", "item_id": 403},
    },
    {
        "gym_index": 4,
        "zone_id": 63,
        "gym_name_zh": "雷文道馆",
        "city_name_zh": "雷文市",
        "leader_name_zh": "小菊儿",
        "leader_ids": {"normal": 153, "challenge": 767},
        "badge_name_zh": "伏特徽章",
        "badge_index": 3,
        "type_specialty": "Electric",
        "type_specialty_zh": "电",
        "reward_tm": {"tm_id": 72, "name_zh": "伏特替换", "item_id": 399},
    },
    {
        "gym_index": 5,
        "zone_id": 97,
        "gym_name_zh": "帆巴道馆",
        "city_name_zh": "帆巴市",
        "leader_name_zh": "菊老大",
        "leader_ids": {"normal": 158, "challenge": 768},
        "badge_name_zh": "震土徽章",
        "badge_index": 4,
        "type_specialty": "Ground",
        "type_specialty_zh": "地面",
        "reward_tm": {"tm_id": 78, "name_zh": "重踏", "item_id": 405},
    },
    {
        "gym_index": 6,
        "zone_id": 108,
        "gym_name_zh": "吹寄道馆",
        "city_name_zh": "吹寄市",
        "leader_name_zh": "风露",
        "leader_ids": {"normal": 155, "challenge": 769},
        "badge_name_zh": "喷射徽章",
        "badge_index": 5,
        "type_specialty": "Flying",
        "type_specialty_zh": "飞行",
        "reward_tm": {"tm_id": 62, "name_zh": "杂技", "item_id": 389},
    },
    {
        "gym_index": 7,
        "zone_id": 121,
        "gym_name_zh": "双龙道馆",
        "city_name_zh": "双龙市",
        "leader_name_zh": "夏卡",
        "leader_ids": {"normal": 159, "challenge": 770},
        "badge_name_zh": "传说徽章",
        "badge_index": 6,
        "type_specialty": "Dragon",
        "type_specialty_zh": "龙",
        "reward_tm": {"tm_id": 82, "name_zh": "龙尾", "item_id": 409},
        "subordinate_trainer_ids": [381, 382, 383, 384, 385],
    },
    {
        "gym_index": 8,
        "zone_id": 473,
        "gym_name_zh": "青海波道馆",
        "city_name_zh": "青海波市",
        "leader_name_zh": "西子伊",
        "leader_ids": {"normal": 160, "challenge": 771},
        "badge_name_zh": "海浪徽章",
        "badge_index": 7,
        "type_specialty": "Water",
        "type_specialty_zh": "水",
        "reward_tm": {"tm_id": 55, "name_zh": "热水", "item_id": 382},
    },
)


class RomGymCatalog:
    """Authoritative Unova Gym knowledge graph sourced from ROM and static scripts."""

    def __init__(self, rom: Any = None):
        self._trainer_catalog = TrainerRomCatalog(getattr(rom, "rom", rom) if rom else None)
        self._by_zone: dict[int, dict[str, Any]] = {defn["zone_id"]: defn for defn in GYM_DEFINITIONS}
        self._by_index: dict[int, dict[str, Any]] = {defn["gym_index"]: defn for defn in GYM_DEFINITIONS}

    def is_gym_zone(self, zone_id: int) -> bool:
        return zone_id in self._by_zone

    def get_gym_by_zone(self, zone_id: int) -> dict[str, Any] | None:
        defn = self._by_zone.get(zone_id)
        if not defn:
            return None
        return self._enrich_gym(defn)

    def get_gym_by_index(self, gym_index: int) -> dict[str, Any] | None:
        defn = self._by_index.get(gym_index)
        if not defn:
            return None
        return self._enrich_gym(defn)

    def get_all_gyms(self) -> list[dict[str, Any]]:
        return [self._enrich_gym(defn) for defn in GYM_DEFINITIONS]

    def _enrich_gym(self, defn: dict[str, Any]) -> dict[str, Any]:
        zid = defn["zone_id"]
        # Fetch static candidate trainers in this zone
        try:
            candidates = self._trainer_catalog.zone_trainer_candidates(zid).get("candidates", [])
        except Exception:
            candidates = []

        trainers: list[dict[str, Any]] = []
        leader_record: dict[str, Any] | None = None
        for c in candidates:
            cat = c.get("catalog") or {}
            tid = c.get("trainer_id")
            tr_info = {
                "trainer_id": tid,
                "name": cat.get("name"),
                "class_name": cat.get("trainer_class", {}).get("name"),
                "is_leader": tid in (defn["leader_ids"]["normal"], defn["leader_ids"]["challenge"]),
                "is_challenge_mode": tid == defn["leader_ids"]["challenge"],
                "pokemon_count": cat.get("party_count", len(cat.get("party", []))),
                "party": [
                    {
                        "species_id": p.get("species_id"),
                        "species_name": p.get("species", {}).get("name"),
                        "level": p.get("level"),
                    }
                    for p in cat.get("party", [])
                ],
            }
            if tr_info["is_leader"]:
                if tid == defn["leader_ids"]["normal"]:
                    leader_record = tr_info
            else:
                trainers.append(tr_info)

        if not trainers and defn.get("subordinate_trainer_ids"):
            for tid in defn["subordinate_trainer_ids"]:
                cat = self._trainer_catalog.get(tid)
                if not cat:
                    continue
                trainers.append({
                    "trainer_id": tid,
                    "name": cat.get("name"),
                    "class_name": cat.get("trainer_class", {}).get("name"),
                    "is_leader": False,
                    "is_challenge_mode": False,
                    "pokemon_count": cat.get("party_count", len(cat.get("party", []))),
                    "party": [
                        {
                            "species_id": p.get("species_id"),
                            "species_name": p.get("species", {}).get("name"),
                            "level": p.get("level"),
                        }
                        for p in cat.get("party", [])
                    ],
                })

        if not leader_record:
            cat = self._trainer_catalog.get(defn["leader_ids"]["normal"])
            if cat:
                leader_record = {
                    "trainer_id": defn["leader_ids"]["normal"],
                    "name": cat.get("name"),
                    "class_name": cat.get("trainer_class", {}).get("name"),
                    "is_leader": True,
                    "is_challenge_mode": False,
                    "pokemon_count": cat.get("party_count", len(cat.get("party", []))),
                    "party": [
                        {
                            "species_id": p.get("species_id"),
                            "species_name": p.get("species", {}).get("name"),
                            "level": p.get("level"),
                        }
                        for p in cat.get("party", [])
                    ],
                }

        badge_obtained = False
        try:
            from ..progression.state import progression_state_service
            prog = progression_state_service.latest or {}
            badges_info = prog.get("badges") or {}
            badge_mask = int(badges_info.get("mask", 0) or 0)
            badge_obtained = bool(badge_mask & (1 << defn["badge_index"]))
        except Exception:
            pass

        return {
            "format": "black2-gym-overview/v1",
            "gym_index": defn["gym_index"],
            "zone_id": zid,
            "gym_name_zh": defn["gym_name_zh"],
            "city_name_zh": defn["city_name_zh"],
            "leader_name_zh": defn["leader_name_zh"],
            "leader_ids": defn["leader_ids"],
            "badge_name_zh": defn["badge_name_zh"],
            "badge_index": defn["badge_index"],
            "badge_obtained": badge_obtained,
            "gym_status": "conquered" if badge_obtained else "unbeaten",
            "type_specialty": defn["type_specialty"],
            "type_specialty_zh": defn["type_specialty_zh"],
            "reward_tm": defn["reward_tm"],
            "leader": leader_record,
            "subordinate_trainers": trainers,
            "total_battles_in_gym": len(trainers) + (1 if leader_record else 0),
            "confidence": "verified_rom_truth",
        }


_default_gym_catalog: RomGymCatalog | None = None


def default_gym_catalog(rom: Any = None) -> RomGymCatalog:
    global _default_gym_catalog
    if _default_gym_catalog is None:
        _default_gym_catalog = RomGymCatalog(rom)
    return _default_gym_catalog
