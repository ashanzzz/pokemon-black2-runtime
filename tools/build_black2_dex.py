#!/usr/bin/env python3
"""Build the versioned, offline Pokemon Black 2 reference dataset.

The input is a checkout of https://github.com/PokeAPI/pokeapi.  Only Python's
standard library is used, and the generated gzip is deterministic for a given
CSV checkout.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SOURCE_COMMIT = "d4f9a4af58ade123fbc0558f68b1c69daa97d9e4"
DATASET_VERSION = "black2-dex-v1-pokeapi-d4f9a4af"
TARGET_GENERATION = 5
TARGET_VERSION_GROUP = 14
MAX_NATIONAL_DEX = 649
MAX_BLACK2_MOVE_ID = 559
MAX_BLACK2_TYPE_ID = 17
LANGUAGES = {9: "en", 12: "zh-Hans"}
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "backend" / "black2" / "dex" / "data" / "black2_dex_v1.json.gz"


def _rows(csv_dir: Path, filename: str) -> Iterable[dict[str, str]]:
    with (csv_dir / filename).open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _integer(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _boolean(value: str | None) -> bool:
    return value == "1"


def _text(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _display_fallback(identifier: str) -> str:
    return identifier.replace("-", " ").title()


def _localized_values(
    csv_dir: Path,
    filename: str,
    id_field: str,
    value_fields: tuple[str, ...],
    *,
    language_field: str = "local_language_id",
) -> dict[int, dict[str, dict[str, str | None]]]:
    values: dict[int, dict[str, dict[str, str | None]]] = defaultdict(dict)
    for row in _rows(csv_dir, filename):
        language = LANGUAGES.get(int(row[language_field]))
        if language is None:
            continue
        values[int(row[id_field])][language] = {
            field: _text(row.get(field)) for field in value_fields
        }
    return values


def _names(
    localized: dict[int, dict[str, dict[str, str | None]]],
    entity_id: int,
    identifier: str,
) -> dict[str, str | None]:
    entity = localized.get(entity_id, {})
    return {
        "en": (entity.get("en") or {}).get("name") or _display_fallback(identifier),
        "zh-Hans": (entity.get("zh-Hans") or {}).get("name"),
    }


def _localized_text(
    localized: dict[int, dict[str, dict[str, str | None]]],
    entity_id: int,
    field: str,
) -> dict[str, str | None]:
    entity = localized.get(entity_id, {})
    return {
        "en": (entity.get("en") or {}).get(field),
        "zh-Hans": (entity.get("zh-Hans") or {}).get(field),
    }


def _id_table(csv_dir: Path, filename: str) -> dict[int, dict[str, str]]:
    return {int(row["id"]): row for row in _rows(csv_dir, filename)}


def _entity_ref(
    entity_id: int,
    table: dict[int, dict[str, str]],
    localized: dict[int, dict[str, dict[str, str | None]]],
) -> dict[str, Any]:
    row = table[entity_id]
    return {
        "id": entity_id,
        "identifier": row["identifier"],
        "names": _names(localized, entity_id, row["identifier"]),
    }


def _resolve_csv_dir(source: Path) -> Path:
    candidates = (source, source / "data" / "v2" / "csv")
    for candidate in candidates:
        if (candidate / "pokemon_species.csv").is_file():
            return candidate.resolve()
    raise SystemExit(f"Could not find data/v2/csv below {source}")


def _git_commit(source: Path) -> str | None:
    checkout = source
    while checkout != checkout.parent and not (checkout / ".git").exists():
        checkout = checkout.parent
    if not (checkout / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _move_value_at_black2(
    row: dict[str, str],
    changes: list[dict[str, str]],
    field: str,
) -> tuple[int | None, int | None]:
    value = _integer(row.get(field))
    applied_at: int | None = None
    for change in sorted(changes, key=lambda item: int(item["changed_in_version_group_id"])):
        changed_at = int(change["changed_in_version_group_id"])
        if changed_at > TARGET_VERSION_GROUP and change.get(field) not in (None, ""):
            value = _integer(change[field])
            applied_at = changed_at
            break
    return value, applied_at


def build_dataset(csv_dir: Path, source_commit: str) -> dict[str, Any]:
    species_rows = {
        int(row["id"]): row
        for row in _rows(csv_dir, "pokemon_species.csv")
        if int(row["id"]) <= MAX_NATIONAL_DEX
    }
    if set(species_rows) != set(range(1, MAX_NATIONAL_DEX + 1)):
        raise RuntimeError("PokeAPI source does not contain the complete National Dex 1-649")

    pokemon_rows = {
        int(row["species_id"]): row
        for row in _rows(csv_dir, "pokemon.csv")
        if int(row["species_id"]) <= MAX_NATIONAL_DEX and _boolean(row["is_default"])
    }
    if set(pokemon_rows) != set(species_rows):
        raise RuntimeError("Every species 1-649 must have exactly one default Pokemon form")

    species_names = _localized_values(
        csv_dir, "pokemon_species_names.csv", "pokemon_species_id", ("name", "genus")
    )
    pokemon_id_to_species = {
        int(row["id"]): int(row["species_id"])
        for row in pokemon_rows.values()
    }
    pokemon_rom_indices: dict[int, set[int]] = defaultdict(set)
    for row in _rows(csv_dir, "pokemon_game_indices.csv"):
        # Version ids 21 and 22 are Black 2 and White 2 in version_group 14.
        species_id = pokemon_id_to_species.get(int(row["pokemon_id"]))
        if species_id is not None and int(row["version_id"]) in (21, 22):
            pokemon_rom_indices[species_id].add(int(row["game_index"]))
    type_rows = _id_table(csv_dir, "types.csv")
    type_names = _localized_values(csv_dir, "type_names.csv", "type_id", ("name",))
    type_rom_indices = {
        int(row["type_id"]): int(row["game_index"])
        for row in _rows(csv_dir, "type_game_indices.csv")
        if int(row["generation_id"]) == TARGET_GENERATION
        and int(row["type_id"]) <= MAX_BLACK2_TYPE_ID
    }
    type_efficacy: dict[tuple[int, int], int] = {}
    for row in _rows(csv_dir, "type_efficacy.csv"):
        damage_type, target_type = int(row["damage_type_id"]), int(row["target_type_id"])
        if damage_type <= MAX_BLACK2_TYPE_ID and target_type <= MAX_BLACK2_TYPE_ID:
            type_efficacy[(damage_type, target_type)] = int(row["damage_factor"])
    past_type_efficacy: dict[tuple[int, int], list[dict[str, str]]] = defaultdict(list)
    for row in _rows(csv_dir, "type_efficacy_past.csv"):
        damage_type, target_type = int(row["damage_type_id"]), int(row["target_type_id"])
        if damage_type <= MAX_BLACK2_TYPE_ID and target_type <= MAX_BLACK2_TYPE_ID:
            past_type_efficacy[(damage_type, target_type)].append(row)
    type_efficacy_history: dict[str, dict[str, int]] = {}
    for pair, candidates in past_type_efficacy.items():
        applicable = sorted(
            (
                candidate
                for candidate in candidates
                if int(candidate["generation_id"]) >= TARGET_GENERATION
            ),
            key=lambda candidate: int(candidate["generation_id"]),
        )
        if applicable:
            source = applicable[0]
            type_efficacy[pair] = int(source["damage_factor"])
            type_efficacy_history[f"{pair[0]}:{pair[1]}"] = {
                "generation_id": int(source["generation_id"]),
                "damage_factor": int(source["damage_factor"]),
            }
    stat_rows = _id_table(csv_dir, "stats.csv")
    stat_names = _localized_values(csv_dir, "stat_names.csv", "stat_id", ("name",))

    ability_rows = {
        int(row["id"]): row
        for row in _rows(csv_dir, "abilities.csv")
        if int(row["generation_id"]) <= TARGET_GENERATION and _boolean(row["is_main_series"])
    }
    ability_names = _localized_values(csv_dir, "ability_names.csv", "ability_id", ("name",))
    ability_prose = _localized_values(
        csv_dir, "ability_prose.csv", "ability_id", ("short_effect", "effect")
    )
    ability_change_rows: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in _rows(csv_dir, "ability_changelog.csv"):
        ability_change_rows[int(row["ability_id"])].append(row)
    ability_change_prose = _localized_values(
        csv_dir,
        "ability_changelog_prose.csv",
        "ability_changelog_id",
        ("effect",),
    )

    move_rows = {
        int(row["id"]): row
        for row in _rows(csv_dir, "moves.csv")
        if int(row["generation_id"]) <= TARGET_GENERATION
        and int(row["id"]) <= MAX_BLACK2_MOVE_ID
    }
    if set(move_rows) != set(range(1, MAX_BLACK2_MOVE_ID + 1)):
        raise RuntimeError("Expected the 559 main-series moves available in Generation V")
    move_names = _localized_values(csv_dir, "move_names.csv", "move_id", ("name",))
    move_prose = _localized_values(
        csv_dir, "move_effect_prose.csv", "move_effect_id", ("short_effect", "effect")
    )
    move_changes: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in _rows(csv_dir, "move_changelog.csv"):
        move_changes[int(row["move_id"])].append(row)
    move_effect_changes: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in _rows(csv_dir, "move_effect_changelog.csv"):
        move_effect_changes[int(row["effect_id"])].append(row)
    move_effect_change_prose = _localized_values(
        csv_dir,
        "move_effect_changelog_prose.csv",
        "move_effect_changelog_id",
        ("effect",),
    )
    damage_rows = _id_table(csv_dir, "move_damage_classes.csv")
    damage_names = _localized_values(
        csv_dir, "move_damage_class_prose.csv", "move_damage_class_id", ("name", "description")
    )
    type_entities: list[dict[str, Any]] = []
    for type_id in range(1, MAX_BLACK2_TYPE_ID + 1):
        type_row = type_rows[type_id]
        damage_class_id = _integer(type_row.get("damage_class_id"))
        type_entities.append(
            {
                "id": type_id,
                "identifier": type_row["identifier"],
                "rom_id": type_rom_indices.get(type_id),
                "generation_id": int(type_row["generation_id"]),
                "names": _names(type_names, type_id, type_row["identifier"]),
                "damage_class": (
                    {
                        **_entity_ref(damage_class_id, damage_rows, damage_names),
                        "description": _localized_text(
                            damage_names, damage_class_id, "description"
                        ),
                    }
                    if damage_class_id
                    else None
                ),
                "efficacy_vs": [
                    {
                        "target_type_id": target_id,
                        "damage_factor": type_efficacy.get((type_id, target_id), 100),
                    }
                    for target_id in range(1, MAX_BLACK2_TYPE_ID + 1)
                ],
                "efficacy_history": {
                    key: value
                    for key, value in type_efficacy_history.items()
                    if key.startswith(f"{type_id}:")
                },
            }
        )
    target_rows = _id_table(csv_dir, "move_targets.csv")
    target_names = _localized_values(
        csv_dir, "move_target_prose.csv", "move_target_id", ("name", "description")
    )
    meta_category_rows = _id_table(csv_dir, "move_meta_categories.csv")
    meta_category_prose = _localized_values(
        csv_dir,
        "move_meta_category_prose.csv",
        "move_meta_category_id",
        ("description",),
    )
    ailment_rows = _id_table(csv_dir, "move_meta_ailments.csv")
    move_meta = {int(row["move_id"]): row for row in _rows(csv_dir, "move_meta.csv")}
    move_stat_changes: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in _rows(csv_dir, "move_meta_stat_changes.csv"):
        move_stat_changes[int(row["move_id"])].append(row)

    item_rows = _id_table(csv_dir, "items.csv")
    item_names = _localized_values(csv_dir, "item_names.csv", "item_id", ("name",))
    item_prose = _localized_values(
        csv_dir, "item_prose.csv", "item_id", ("short_effect", "effect")
    )
    item_flavor = _localized_values(
        csv_dir,
        "item_flavor_text.csv",
        "item_id",
        ("flavor_text",),
        language_field="language_id",
    )
    # Flavor text is versioned; reload only the Black 2 / White 2 rows.
    item_flavor = defaultdict(dict)
    for row in _rows(csv_dir, "item_flavor_text.csv"):
        if int(row["version_group_id"]) != TARGET_VERSION_GROUP:
            continue
        language = LANGUAGES.get(int(row["language_id"]))
        if language:
            item_flavor[int(row["item_id"])][language] = {
                "flavor_text": _text(row["flavor_text"])
            }
    category_rows = _id_table(csv_dir, "item_categories.csv")
    category_names = _localized_values(
        csv_dir, "item_category_prose.csv", "item_category_id", ("name",)
    )
    pocket_rows = _id_table(csv_dir, "item_pockets.csv")
    pocket_names = _localized_values(
        csv_dir, "item_pocket_names.csv", "item_pocket_id", ("name",)
    )
    item_indices: dict[int, set[int]] = defaultdict(set)
    reverse_item_indices: dict[int, set[int]] = defaultdict(set)
    for row in _rows(csv_dir, "item_game_indices.csv"):
        if int(row["generation_id"]) == TARGET_GENERATION:
            item_id, game_index = int(row["item_id"]), int(row["game_index"])
            item_indices[item_id].add(game_index)
            reverse_item_indices[game_index].add(item_id)
    item_price_rows: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in _rows(csv_dir, "item_prices.csv"):
        if int(row["version_group_id"]) == TARGET_VERSION_GROUP:
            item_price_rows[int(row["item_id"])].append(row)

    machines: list[dict[str, Any]] = []
    machine_by_move: dict[int, dict[str, Any]] = {}
    for row in _rows(csv_dir, "machines.csv"):
        if int(row["version_group_id"]) != TARGET_VERSION_GROUP:
            continue
        item_id, move_id = int(row["item_id"]), int(row["move_id"])
        item_identifier = item_rows[item_id]["identifier"]
        machine = {
            "machine_number": int(row["machine_number"]),
            "kind": "hm" if item_identifier.startswith("hm") else "tm",
            "label": item_identifier.upper(),
            "item_id": item_id,
            "item_game_indices": sorted(item_indices.get(item_id, set())),
            "move_id": move_id,
        }
        machines.append(machine)
        machine_by_move[move_id] = machine
    machines.sort(key=lambda value: (value["kind"] == "hm", value["machine_number"]))

    abilities: list[dict[str, Any]] = []
    for ability_id, row in sorted(ability_rows.items()):
        historical = None
        for change in sorted(
            ability_change_rows.get(ability_id, []),
            key=lambda value: int(value["changed_in_version_group_id"]),
        ):
            if int(change["changed_in_version_group_id"]) > TARGET_VERSION_GROUP:
                historical = change
                break
        if historical:
            change_id = int(historical["id"])
            full_effect = _localized_text(ability_change_prose, change_id, "effect")
            short_effect = {"en": None, "zh-Hans": None}
            provenance = {
                "mode": "historical_changelog",
                "changed_in_version_group_id": int(historical["changed_in_version_group_id"]),
            }
        else:
            full_effect = _localized_text(ability_prose, ability_id, "effect")
            short_effect = _localized_text(ability_prose, ability_id, "short_effect")
            provenance = {"mode": "current_prose_no_later_change"}
        abilities.append(
            {
                "id": ability_id,
                "identifier": row["identifier"],
                "rom_id": ability_id,
                "generation_id": int(row["generation_id"]),
                "names": _names(ability_names, ability_id, row["identifier"]),
                "effect": {
                    "short": short_effect,
                    "full": full_effect,
                    "as_of_version_group_id": TARGET_VERSION_GROUP,
                    "provenance": provenance,
                },
            }
        )
    ability_by_id = {row["id"]: row for row in abilities}

    moves: list[dict[str, Any]] = []
    move_by_id: dict[int, dict[str, Any]] = {}
    temporal_fields = (
        "type_id",
        "power",
        "pp",
        "accuracy",
        "priority",
        "target_id",
        "effect_id",
        "effect_chance",
    )
    for move_id, row in sorted(move_rows.items()):
        values: dict[str, int | None] = {}
        adjustments: dict[str, int] = {}
        for field in temporal_fields:
            values[field], changed_at = _move_value_at_black2(
                row, move_changes.get(move_id, []), field
            )
            if changed_at is not None:
                adjustments[field] = changed_at
        effect_id = values["effect_id"]
        historical_effect = None
        if effect_id is not None:
            for change in sorted(
                move_effect_changes.get(effect_id, []),
                key=lambda value: int(value["changed_in_version_group_id"]),
            ):
                if int(change["changed_in_version_group_id"]) > TARGET_VERSION_GROUP:
                    historical_effect = change
                    break
        if historical_effect:
            change_id = int(historical_effect["id"])
            effect_full = _localized_text(move_effect_change_prose, change_id, "effect")
            effect_short = {"en": None, "zh-Hans": None}
            effect_provenance = {
                "mode": "historical_changelog",
                "changed_in_version_group_id": int(
                    historical_effect["changed_in_version_group_id"]
                ),
            }
        elif effect_id is not None:
            effect_full = _localized_text(move_prose, effect_id, "effect")
            effect_short = _localized_text(move_prose, effect_id, "short_effect")
            effect_provenance = {"mode": "current_prose_no_later_change"}
        else:
            effect_full = {"en": None, "zh-Hans": None}
            effect_short = {"en": None, "zh-Hans": None}
            effect_provenance = {"mode": "unavailable"}

        type_id = values["type_id"]
        damage_id = int(row["damage_class_id"])
        target_id = values["target_id"]
        meta = move_meta.get(move_id)
        if meta:
            category_id = int(meta["meta_category_id"])
            ailment_id = int(meta["meta_ailment_id"])
            meta_value: dict[str, Any] | None = {
                "category": {
                    "id": category_id,
                    "identifier": meta_category_rows[category_id]["identifier"],
                    "description": _localized_text(
                        meta_category_prose, category_id, "description"
                    ),
                },
                "ailment": {
                    "id": ailment_id,
                    "identifier": ailment_rows[ailment_id]["identifier"],
                },
                **{
                    field: _integer(meta[field])
                    for field in (
                        "min_hits",
                        "max_hits",
                        "min_turns",
                        "max_turns",
                        "drain",
                        "healing",
                        "crit_rate",
                        "ailment_chance",
                        "flinch_chance",
                        "stat_chance",
                    )
                },
                "stat_changes": [
                    {
                        "stat": _entity_ref(
                            int(change["stat_id"]), stat_rows, stat_names
                        ),
                        "change": int(change["change"]),
                    }
                    for change in move_stat_changes.get(move_id, [])
                ],
                "temporal_scope": "PokeAPI move_meta has no version-group changelog",
            }
        else:
            meta_value = None

        move = {
            "id": move_id,
            "identifier": row["identifier"],
            "rom_id": move_id,
            "generation_id": int(row["generation_id"]),
            "names": _names(move_names, move_id, row["identifier"]),
            "type": _entity_ref(type_id, type_rows, type_names) if type_id else None,
            "power": values["power"],
            "accuracy": values["accuracy"],
            "pp": values["pp"],
            "priority": values["priority"],
            "damage_class": {
                **_entity_ref(damage_id, damage_rows, damage_names),
                "description": _localized_text(damage_names, damage_id, "description"),
            },
            "target": (
                {
                    **_entity_ref(target_id, target_rows, target_names),
                    "description": _localized_text(target_names, target_id, "description"),
                }
                if target_id
                else None
            ),
            "effect_chance": values["effect_chance"],
            "effect": {
                "id": effect_id,
                "short": effect_short,
                "full": effect_full,
                "as_of_version_group_id": TARGET_VERSION_GROUP,
                "provenance": effect_provenance,
            },
            "meta": meta_value,
            "machines": [machine_by_move[move_id]] if move_id in machine_by_move else [],
            "learned_by": [],
            "as_of_version_group_id": TARGET_VERSION_GROUP,
            "historical_numeric_adjustments": adjustments,
        }
        moves.append(move)
        move_by_id[move_id] = move

    items: list[dict[str, Any]] = []
    for item_id in sorted(item_indices):
        row = item_rows.get(item_id)
        if row is None:
            raise RuntimeError(f"Missing items.csv row for Generation V item {item_id}")
        indices = sorted(item_indices[item_id])
        category_id = int(row["category_id"])
        category = category_rows[category_id]
        pocket_id = int(category["pocket_id"])
        price_rows = item_price_rows.get(item_id, [])
        version_prices = [
            {
                "currency_id": int(price["currency_id"]),
                "purchase": _integer(price["purchase_price"]),
                "sell": _integer(price["sell_price"]),
            }
            for price in price_rows
        ]
        items.append(
            {
                "id": item_id,
                "identifier": row["identifier"],
                "names": _names(item_names, item_id, row["identifier"]),
                "generation_id": TARGET_GENERATION,
                "game_index": indices[0] if len(indices) == 1 else None,
                "game_indices": indices,
                "game_index_ambiguous": len(indices) != 1,
                "category": {
                    "id": category_id,
                    "identifier": category["identifier"],
                    "names": _names(category_names, category_id, category["identifier"]),
                },
                "pocket": {
                    "id": pocket_id,
                    "identifier": pocket_rows[pocket_id]["identifier"],
                    "names": _names(
                        pocket_names, pocket_id, pocket_rows[pocket_id]["identifier"]
                    ),
                },
                "price": {
                    "pokeapi_cost": int(row["cost"]),
                    "black2_white2_versioned": version_prices,
                    "versioned_price_available": bool(version_prices),
                },
                "fling": {
                    "power": _integer(row["fling_power"]),
                    "effect_id": _integer(row["fling_effect_id"]),
                },
                "effect": {
                    "short": _localized_text(item_prose, item_id, "short_effect"),
                    "full": _localized_text(item_prose, item_id, "effect"),
                    "temporal_scope": "PokeAPI item prose is not versioned",
                },
                "flavor_text": _localized_text(item_flavor, item_id, "flavor_text"),
                "machine": next(
                    (machine for machine in machines if machine["item_id"] == item_id), None
                ),
            }
        )

    current_types: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in _rows(csv_dir, "pokemon_types.csv"):
        current_types[int(row["pokemon_id"])].append(row)
    past_types: dict[int, dict[int, list[dict[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in _rows(csv_dir, "pokemon_types_past.csv"):
        past_types[int(row["pokemon_id"])][int(row["generation_id"])].append(row)

    current_stats: dict[int, dict[int, dict[str, str]]] = defaultdict(dict)
    for row in _rows(csv_dir, "pokemon_stats.csv"):
        current_stats[int(row["pokemon_id"])][int(row["stat_id"])] = row
    past_stats: dict[tuple[int, int], list[dict[str, str]]] = defaultdict(list)
    for row in _rows(csv_dir, "pokemon_stats_past.csv"):
        past_stats[(int(row["pokemon_id"]), int(row["stat_id"]))].append(row)

    current_abilities: dict[int, dict[int, dict[str, str]]] = defaultdict(dict)
    for row in _rows(csv_dir, "pokemon_abilities.csv"):
        current_abilities[int(row["pokemon_id"])][int(row["slot"])] = row
    past_abilities: dict[tuple[int, int], list[dict[str, str]]] = defaultdict(list)
    for row in _rows(csv_dir, "pokemon_abilities_past.csv"):
        past_abilities[(int(row["pokemon_id"]), int(row["slot"]))].append(row)

    move_methods = _id_table(csv_dir, "pokemon_move_methods.csv")
    default_pokemon_to_species = {
        int(row["id"]): species_id for species_id, row in pokemon_rows.items()
    }
    learnsets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    learnset_seen: dict[int, set[tuple[int, int, int, int | None]]] = defaultdict(set)
    for row in _rows(csv_dir, "pokemon_moves.csv"):
        if int(row["version_group_id"]) != TARGET_VERSION_GROUP:
            continue
        pokemon_id = int(row["pokemon_id"])
        species_id = default_pokemon_to_species.get(pokemon_id)
        move_id = int(row["move_id"])
        if species_id is None or move_id not in move_by_id:
            continue
        method_id = int(row["pokemon_move_method_id"])
        level = int(row["level"])
        order = _integer(row.get("order"))
        dedupe_key = (move_id, method_id, level, order)
        if dedupe_key in learnset_seen[species_id]:
            continue
        learnset_seen[species_id].add(dedupe_key)
        method_identifier = move_methods[method_id]["identifier"]
        entry = {
            "move_id": move_id,
            "move_identifier": move_by_id[move_id]["identifier"],
            "move_names": move_by_id[move_id]["names"],
            "method": {"id": method_id, "identifier": method_identifier},
            "level": level if method_identifier == "level-up" else None,
            "order": order,
            "machine": machine_by_move.get(move_id) if method_identifier == "machine" else None,
        }
        learnsets[species_id].append(entry)
        move_by_id[move_id]["learned_by"].append(
            {
                "pokemon_id": species_id,
                "method": method_identifier,
                "level": entry["level"],
                "machine": entry["machine"],
            }
        )

    method_order = {"level-up": 0, "machine": 1, "tutor": 2, "egg": 3}
    pokemon: list[dict[str, Any]] = []
    for species_id, species in sorted(species_rows.items()):
        form = pokemon_rows[species_id]
        pokemon_id = int(form["id"])

        type_generation = None
        historical_type_generations = sorted(
            generation
            for generation in past_types.get(pokemon_id, {})
            if generation >= TARGET_GENERATION
        )
        if historical_type_generations:
            type_generation = historical_type_generations[0]
            type_source_rows = past_types[pokemon_id][type_generation]
        else:
            type_source_rows = current_types[pokemon_id]
        types = [
            {
                "slot": int(type_row["slot"]),
                **_entity_ref(int(type_row["type_id"]), type_rows, type_names),
            }
            for type_row in sorted(type_source_rows, key=lambda value: int(value["slot"]))
        ]

        stats: list[dict[str, Any]] = []
        for stat_id in range(1, 7):
            stat = current_stats[pokemon_id][stat_id]
            historical_candidates = sorted(
                (
                    candidate
                    for candidate in past_stats.get((pokemon_id, stat_id), [])
                    if int(candidate["generation_id"]) >= TARGET_GENERATION
                ),
                key=lambda value: int(value["generation_id"]),
            )
            source = historical_candidates[0] if historical_candidates else stat
            stats.append(
                {
                    **_entity_ref(stat_id, stat_rows, stat_names),
                    "base_stat": int(source["base_stat"]),
                    "effort": int(source["effort"]),
                    "historical_generation_id": (
                        int(source["generation_id"]) if historical_candidates else None
                    ),
                }
            )

        ability_slots = dict(current_abilities[pokemon_id])
        for slot in set(ability_slots) | {
            slot for candidate_id, slot in past_abilities if candidate_id == pokemon_id
        }:
            historical_candidates = sorted(
                (
                    candidate
                    for candidate in past_abilities.get((pokemon_id, slot), [])
                    if int(candidate["generation_id"]) >= TARGET_GENERATION
                ),
                key=lambda value: int(value["generation_id"]),
            )
            if historical_candidates:
                source = historical_candidates[0]
                if source["ability_id"]:
                    ability_slots[slot] = source
                else:
                    ability_slots.pop(slot, None)
        pokemon_abilities = []
        for slot, ability in sorted(ability_slots.items()):
            ability_id = int(ability["ability_id"])
            details = ability_by_id.get(ability_id)
            if details is None:
                raise RuntimeError(
                    f"Generation V Pokemon {species_id} references unavailable ability {ability_id}"
                )
            pokemon_abilities.append(
                {
                    "slot": slot,
                    "is_hidden": _boolean(ability["is_hidden"]),
                    "id": ability_id,
                    "identifier": details["identifier"],
                    "names": details["names"],
                }
            )

        entries = learnsets.get(species_id, [])
        entries.sort(
            key=lambda entry: (
                method_order.get(entry["method"]["identifier"], 99),
                entry["level"] if entry["level"] is not None else 0,
                entry["move_id"],
            )
        )
        method_counts = Counter(entry["method"]["identifier"] for entry in entries)
        pokemon.append(
            {
                "id": species_id,
                "pokedex_number": species_id,
                "pokemon_id": pokemon_id,
                "rom_game_indices": sorted(pokemon_rom_indices.get(species_id, set())),
                "identifier": species["identifier"],
                "names": _names(species_names, species_id, species["identifier"]),
                "genera": _localized_text(species_names, species_id, "genus"),
                "generation_id": int(species["generation_id"]),
                "is_legendary": _boolean(species["is_legendary"]),
                "is_mythical": _boolean(species["is_mythical"]),
                "dimensions": {
                    "height_decimetres": int(form["height"]),
                    "weight_hectograms": int(form["weight"]),
                },
                "base_experience": _integer(form["base_experience"]),
                "types": types,
                "base_stats": stats,
                "abilities": pokemon_abilities,
                "generation5_type_override": type_generation,
                "learnset": {
                    "version_group_id": TARGET_VERSION_GROUP,
                    "version_group": "black-2-white-2",
                    "count": len(entries),
                    "counts_by_method": dict(sorted(method_counts.items())),
                    "entries": entries,
                },
            }
        )

    for move in moves:
        move["learned_by"].sort(
            key=lambda entry: (
                entry["pokemon_id"],
                method_order.get(entry["method"], 99),
                entry["level"] or 0,
            )
        )

    item_index_collisions = {
        str(game_index): sorted(item_ids)
        for game_index, item_ids in sorted(reverse_item_indices.items())
        if len(item_ids) > 1
    }
    item_multi_indices = {
        str(item_id): sorted(indices)
        for item_id, indices in sorted(item_indices.items())
        if len(indices) > 1
    }
    missing_zh = {
        "pokemon": sum(1 for row in pokemon if not row["names"]["zh-Hans"]),
        "moves": sum(1 for row in moves if not row["names"]["zh-Hans"]),
        "items": sum(1 for row in items if not row["names"]["zh-Hans"]),
        "abilities": sum(1 for row in abilities if not row["names"]["zh-Hans"]),
        "types": sum(1 for row in type_entities if not row["names"]["zh-Hans"]),
    }

    return {
        "format": "black2-offline-dex/v1",
        "dataset_version": DATASET_VERSION,
        "source_commit": source_commit,
        "game": {
            "title": "Pokemon Black 2 / White 2",
            "generation_id": TARGET_GENERATION,
            "version_group_id": TARGET_VERSION_GROUP,
            "version_group": "black-2-white-2",
            "national_dex_range": {"minimum": 1, "maximum": MAX_NATIONAL_DEX},
        },
        "source": {
            "name": "PokeAPI/pokeapi CSV database",
            "url": "https://github.com/PokeAPI/pokeapi",
            "commit": source_commit,
            "license": "BSD-3-Clause",
            "runtime_network_required": False,
        },
        "rom_provenance": {
            "title": "Pokemon Black 2 / White 2 (Nintendo DS)",
            "format": "NDS",
            "status": "external_local_rom_reference",
            "bundled": False,
            "sha256": None,
            "resource_root": "Configured emulator ROM and reverse-engineering assets",
            "linked_fields": [
                "items.game_indices",
                "moves.id",
                "abilities.id",
                "pokemon.pokedex_number",
            ],
            "note": "The offline Dex contains no ROM bytes; ROM provenance is retained so runtime decoders can state which resource supplied an observation.",
        },
        "id_spaces": {
            "pokemon.id": "National Pokedex / PokeAPI species id, 1-649",
            "pokemon.pokemon_id": "PokeAPI default-form pokemon id",
            "moves.id": "PokeAPI main-series move id; Generation V range 1-559",
            "abilities.id": "PokeAPI ability id; Generation V main-series range 1-164",
            "items.id": "PokeAPI item id",
            "items.game_indices": "Generation V in-game item indices; source contains documented ambiguities",
            "types.id": "Generation V main-series type id, 1-17",
        },
        "id_space_ranges": {
            "pokemon": {"minimum": 1, "maximum": 649, "key": "national_dex"},
            "moves": {"minimum": 1, "maximum": 559, "key": "pokeapi_move_id"},
            "abilities": {"minimum": 1, "maximum": 164, "key": "pokeapi_ability_id"},
            "items": {"key": "pokeapi_item_id", "generation": 5, "game_index_field": "game_indices"},
            "types": {"minimum": 1, "maximum": 17, "key": "gen5_type_id"},
        },
        "rom_id_spaces": {
            "pokemon": {
                "field": "rom_game_indices",
                "version_ids": [21, 22],
                "meaning": "Black 2 and White 2 PokeAPI version game indices",
            },
            "moves": {
                "field": "rom_id",
                "range": {"minimum": 1, "maximum": 559},
                "meaning": "Generation V main-series move id; equal to the PokeAPI id in this dataset",
            },
            "abilities": {
                "field": "rom_id",
                "range": {"minimum": 1, "maximum": 164},
                "meaning": "Generation V main-series ability id; equal to the PokeAPI id in this dataset",
            },
            "items": {
                "field": "game_indices",
                "generation": 5,
                "meaning": "Generation V item table index; candidates are retained when the source is ambiguous",
            },
            "types": {
                "field": "rom_id",
                "range": {"minimum": 0, "maximum": 16},
                "meaning": "Generation V zero-based type table index",
            },
        },
        "temporal_scope": {
            "pokemon": "Default forms with type, base-stat, and ability past values restored for Generation V",
            "moves": "Numeric values and effect prose restored as of version group 14 where PokeAPI changelogs exist",
            "move_meta": "Current PokeAPI metadata; the source has no version-group history for these fields",
            "items": "Generation V game indices and version-group-14 prices where present; pokeapi_cost is also retained",
            "learnsets": "Exact PokeAPI pokemon_moves rows for version group 14 and default forms",
        },
        "counts": {
            "pokemon": len(pokemon),
            "moves": len(moves),
            "items": len(items),
            "abilities": len(abilities),
            "types": len(type_entities),
            "machines": len(machines),
            "learnset_entries": sum(row["learnset"]["count"] for row in pokemon),
        },
        "quality": {
            "missing_zh_Hans_names": missing_zh,
            "item_ids_with_multiple_generation5_indices": item_multi_indices,
            "generation5_indices_with_multiple_item_ids": item_index_collisions,
        },
        "entities": {
            "pokemon": pokemon,
            "moves": moves,
            "items": items,
            "abilities": abilities,
            "types": type_entities,
            "machines": machines,
        },
    }


def write_dataset(dataset: dict[str, Any], output: Path, *, pretty: bool = False) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    indent = 2 if pretty else None
    separators = None if pretty else (",", ":")
    payload = json.dumps(
        dataset,
        ensure_ascii=False,
        indent=indent,
        separators=separators,
    ).encode("utf-8")
    if output.suffix == ".gz":
        with output.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as archive:
                archive.write(payload)
    else:
        output.write_bytes(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        help="PokeAPI checkout root or its data/v2/csv directory",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-commit", default=SOURCE_COMMIT)
    parser.add_argument(
        "--allow-source-mismatch",
        action="store_true",
        help="Build from a different checkout while recording its actual commit",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    csv_dir = _resolve_csv_dir(args.source)
    actual_commit = _git_commit(csv_dir)
    source_commit = actual_commit or args.source_commit
    if actual_commit and actual_commit != args.source_commit and not args.allow_source_mismatch:
        raise SystemExit(
            f"PokeAPI checkout is {actual_commit}, expected {args.source_commit}; "
            "use --allow-source-mismatch to build and record another revision"
        )
    dataset = build_dataset(csv_dir, source_commit)
    write_dataset(dataset, args.output, pretty=args.pretty)
    counts = dataset["counts"]
    print(f"Wrote {args.output.resolve()}")
    print(
        "Counts: "
        + ", ".join(f"{name}={value}" for name, value in counts.items())
    )


if __name__ == "__main__":
    main()
