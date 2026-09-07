"""Lazy, read-only access to the generated Black 2 Dex dataset.

The runtime deliberately never reaches out to PokeAPI.  ``build_black2_dex``
turns the source CSV checkout into one versioned gzip; this module only reads
that artifact and builds in-memory indexes once per process.
"""
from __future__ import annotations

import copy
import gzip
import json
import threading
import unicodedata
from pathlib import Path
from typing import Any, Iterable


DATA_PATH = Path(__file__).resolve().parent / "data" / "black2_dex_v1.json.gz"
ENTITY_KINDS = ("pokemon", "moves", "items", "abilities", "types")
ENTITY_SINGULAR = {
    "pokemon": "pokemon",
    "moves": "move",
    "items": "item",
    "abilities": "ability",
    "types": "type",
}


class DexDataError(RuntimeError):
    """Raised when the generated offline artifact is unavailable or invalid."""


def _normalise(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value)).casefold().strip()


def _normalise_query(value: Any) -> str:
    query = _normalise(value)
    if query.startswith("#"):
        query = query[1:].strip()
    # National Dex callers commonly send #001/#649; entity search stores the
    # canonical integer, so remove only numeric leading zeroes here.
    if query.isdigit():
        query = str(int(query))
    return query


def _dataset_ref(document: dict[str, Any]) -> dict[str, Any]:
    source = document.get("source") or {}
    return {
        "dataset_version": document.get("dataset_version"),
        "source_commit": source.get("commit"),
        "source": {
            "name": source.get("name"),
            "commit": source.get("commit"),
            "license": source.get("license"),
            "runtime_network_required": source.get("runtime_network_required", False),
        },
        "game": document.get("game"),
        "id_spaces": document.get("id_spaces"),
        "id_space_ranges": document.get("id_space_ranges"),
        "rom_id_spaces": document.get("rom_id_spaces"),
        "rom_provenance": document.get("rom_provenance"),
        "version_scope": document.get("temporal_scope"),
    }


class DexStore:
    def __init__(self, path: Path = DATA_PATH):
        self.path = Path(path)
        self._document: dict[str, Any] | None = None
        self._indexes: dict[str, dict[int, dict[str, Any]]] = {}
        self._item_game_index: dict[int, list[dict[str, Any]]] = {}
        self._rom_index: dict[str, dict[int, list[dict[str, Any]]]] = {}
        self._lock = threading.RLock()

    def _load(self) -> None:
        if self._document is not None:
            return
        if not self.path.is_file():
            raise DexDataError(f"Offline Dex artifact not found: {self.path}")
        try:
            opener = gzip.open if self.path.suffix == ".gz" else open
            with opener(self.path, "rt", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError, UnicodeError) as exc:
            raise DexDataError(f"Cannot load offline Dex artifact: {exc}") from exc
        if document.get("format") != "black2-offline-dex/v1":
            raise DexDataError("Unsupported offline Dex format")
        entities = document.get("entities")
        if not isinstance(entities, dict) or any(kind not in entities for kind in ENTITY_KINDS):
            raise DexDataError("Offline Dex artifact is missing an entity collection")
        indexes: dict[str, dict[int, dict[str, Any]]] = {}
        for kind in ENTITY_KINDS:
            index: dict[int, dict[str, Any]] = {}
            for entity in entities[kind]:
                try:
                    entity_id = int(entity["id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise DexDataError(f"Invalid {kind} entry in offline Dex artifact") from exc
                index[entity_id] = entity
            indexes[kind] = index
        item_index: dict[int, list[dict[str, Any]]] = {}
        for item in entities["items"]:
            for game_index in item.get("game_indices", []):
                item_index.setdefault(int(game_index), []).append(item)
        for candidates in item_index.values():
            candidates.sort(key=lambda item: int(item["id"]))
        rom_index: dict[str, dict[int, list[dict[str, Any]]]] = {
            kind: {} for kind in ENTITY_KINDS
        }
        for kind in ENTITY_KINDS:
            for entity in entities[kind]:
                if kind == "items":
                    rom_ids = entity.get("game_indices", [])
                elif kind == "pokemon":
                    rom_ids = entity.get("rom_game_indices", [])
                else:
                    rom_id = entity.get("rom_id")
                    rom_ids = [] if rom_id is None else [rom_id]
                for rom_id in rom_ids:
                    rom_index[kind].setdefault(int(rom_id), []).append(entity)
        for index in rom_index.values():
            for candidates in index.values():
                candidates.sort(key=lambda item: int(item["id"]))
        self._document = document
        self._indexes = indexes
        self._item_game_index = item_index
        self._rom_index = rom_index

    def _ensure(self) -> None:
        with self._lock:
            self._load()

    @property
    def document(self) -> dict[str, Any]:
        self._ensure()
        assert self._document is not None
        return self._document

    def dataset_ref(self) -> dict[str, Any]:
        return copy.deepcopy(_dataset_ref(self.document))

    def summary(self) -> dict[str, Any]:
        document = self.document
        return {
            "format": "black2-dex-summary/v1",
            "dataset": _dataset_ref(document),
            "source_commit": (document.get("source") or {}).get("commit"),
            "version_scope": copy.deepcopy(document.get("temporal_scope", {})),
            "id_spaces": copy.deepcopy(document.get("id_spaces", {})),
            "rom_provenance": copy.deepcopy(document.get("rom_provenance")),
            "counts": copy.deepcopy(document.get("counts", {})),
            "quality": copy.deepcopy(document.get("quality", {})),
            "temporal_scope": copy.deepcopy(document.get("temporal_scope", {})),
            "available_entities": list(ENTITY_KINDS),
            "endpoints": {
                "pokemon": "/api/v1/dex/pokemon",
                "moves": "/api/v1/dex/moves",
                "items": "/api/v1/dex/items",
                "abilities": "/api/v1/dex/abilities",
                "types": "/api/v1/dex/types",
            },
        }

    @staticmethod
    def _searchable(entity: dict[str, Any]) -> str:
        names = entity.get("names") or {}
        values = [entity.get("identifier", ""), names.get("en", ""), names.get("zh-Hans", "")]
        if entity.get("pokedex_number") is not None:
            values.append(str(entity["pokedex_number"]))
        return " ".join(_normalise(value) for value in values if value is not None)

    @staticmethod
    def _summary_entity(kind: str, entity: dict[str, Any]) -> dict[str, Any]:
        if kind == "pokemon":
            return {
                "id": entity["id"],
                "pokedex_number": entity["pokedex_number"],
                "pokemon_id": entity["pokemon_id"],
                "rom_game_indices": entity.get("rom_game_indices", []),
                "identifier": entity["identifier"],
                "names": entity["names"],
                "generation_id": entity["generation_id"],
                "types": entity["types"],
                "abilities": entity["abilities"],
                "base_stats": entity["base_stats"],
                "learnset_count": entity["learnset"]["count"],
            }
        if kind == "moves":
            return {
                "id": entity["id"],
                "identifier": entity["identifier"],
                "rom_id": entity.get("rom_id"),
                "names": entity["names"],
                "generation_id": entity["generation_id"],
                "type": entity["type"],
                "power": entity["power"],
                "accuracy": entity["accuracy"],
                "pp": entity["pp"],
                "priority": entity["priority"],
                "damage_class": entity["damage_class"],
                "target": entity["target"],
                "effect_chance": entity["effect_chance"],
                "machines": entity["machines"],
            }
        if kind == "items":
            return {
                "id": entity["id"],
                "identifier": entity["identifier"],
                "names": entity["names"],
                "game_index": entity["game_index"],
                "game_indices": entity["game_indices"],
                "game_index_ambiguous": entity["game_index_ambiguous"],
                "category": entity["category"],
                "price": entity["price"],
                "machine": entity["machine"],
            }
        if kind == "types":
            return {
                "id": entity["id"],
                "identifier": entity["identifier"],
                "rom_id": entity.get("rom_id"),
                "names": entity["names"],
                "generation_id": entity["generation_id"],
                "damage_class": entity["damage_class"],
                "efficacy_vs": entity["efficacy_vs"],
            }
        return {
            "id": entity["id"],
            "identifier": entity["identifier"],
            "rom_id": entity.get("rom_id"),
            "names": entity["names"],
            "generation_id": entity["generation_id"],
            "effect": entity["effect"],
        }

    def list(
        self,
        kind: str,
        *,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
        game_index: int | None = None,
        rom_id: int | None = None,
    ) -> dict[str, Any]:
        self._ensure()
        if kind not in ENTITY_KINDS:
            raise ValueError(f"Unknown Dex entity kind: {kind}")
        candidates: Iterable[dict[str, Any]]
        if game_index is not None and rom_id is not None:
            raise ValueError("game_index and rom_id filters are mutually exclusive")
        if rom_id is not None:
            candidates = self._rom_index.get(kind, {}).get(rom_id, [])
        elif game_index is not None:
            if kind != "items":
                raise ValueError("game_index filtering is only available for items")
            candidates = self._item_game_index.get(game_index, [])
        else:
            assert self._document is not None
            candidates = self._document["entities"][kind]
        needle = _normalise_query(q) if q else ""
        if needle:
            candidates = (entity for entity in candidates if needle in self._searchable(entity))
        matched = list(candidates)
        page = matched[offset : offset + limit]
        return {
            "format": "black2-dex-list/v1",
            "entity": kind,
            "dataset": self.dataset_ref(),
            "query": {"q": q, "game_index": game_index, "rom_id": rom_id},
            "pagination": {
                "offset": offset,
                "limit": limit,
                "matched": len(matched),
                "total": len(matched),
                "returned": len(page),
                "next_offset": offset + limit if offset + limit < len(matched) else None,
            },
            "items": [self._summary_entity(kind, entity) for entity in page],
        }

    def search_all(
        self,
        q: str,
        *,
        limit: int = 50,
        offset: int = 0,
        kinds: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Search every catalog entity with one stable, globally paged result set."""
        self._ensure()
        selected = tuple(kinds or ENTITY_KINDS)
        invalid = [kind for kind in selected if kind not in ENTITY_KINDS]
        if invalid:
            raise ValueError(f"Unknown Dex entity kinds: {', '.join(invalid)}")
        needle = _normalise_query(q)
        assert self._document is not None
        matched: list[tuple[str, dict[str, Any]]] = []
        for kind in selected:
            for entity in self._document["entities"][kind]:
                if needle and needle not in self._searchable(entity):
                    continue
                matched.append((kind, entity))
        page = matched[offset : offset + limit]
        results = []
        for kind, entity in page:
            results.append(
                {
                    "entity": kind,
                    "entity_type": ENTITY_SINGULAR[kind],
                    "id": entity["id"],
                    "identifier": entity["identifier"],
                    "names": entity["names"],
                    "result": self._summary_entity(kind, entity),
                }
            )
        return {
            "format": "black2-catalog-search/v1",
            "dataset": self.dataset_ref(),
            "query": q,
            "pagination": {
                "offset": offset,
                "limit": limit,
                "matched": len(matched),
                "total": len(matched),
                "returned": len(page),
                "next_offset": offset + limit if offset + limit < len(matched) else None,
            },
            "results": results,
        }

    def get(self, kind: str, entity_id: int) -> dict[str, Any] | None:
        self._ensure()
        if kind not in ENTITY_KINDS:
            raise ValueError(f"Unknown Dex entity kind: {kind}")
        entity = self._indexes[kind].get(entity_id)
        return copy.deepcopy(entity) if entity is not None else None

    def items_by_game_index(self, game_index: int) -> list[dict[str, Any]]:
        self._ensure()
        return [copy.deepcopy(item) for item in self._item_game_index.get(game_index, [])]

    def entities_by_rom_id(self, kind: str, rom_id: int) -> list[dict[str, Any]]:
        self._ensure()
        if kind not in ENTITY_KINDS:
            raise ValueError(f"Unknown Dex entity kind: {kind}")
        return [copy.deepcopy(entity) for entity in self._rom_index[kind].get(rom_id, [])]


dex_store = DexStore()

__all__ = [
    "DATA_PATH",
    "ENTITY_KINDS",
    "ENTITY_SINGULAR",
    "DexDataError",
    "DexStore",
    "dex_store",
]
