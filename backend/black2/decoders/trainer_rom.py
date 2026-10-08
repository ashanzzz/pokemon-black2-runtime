"""Read-only Black 2 trainer catalog from the current ROM.

The runtime battle heap does not yet expose a verified trainer-id pointer, so
this module is deliberately a *catalog* rather than a live classifier.  Once
an encounter/script decoder supplies a trainer id, the catalog resolves:

* TRData (``a/0/9/1``): battle type, trainer class, party count and flags;
* TRPoke (``a/0/9/2``): trainer Pokémon species/form/level/items/moves;
* message file 382: trainer name/variation;
* message file 383: trainer-class display name.

The Gen-5 message codec follows the public PPRE implementation, but all
values are decoded from the local ROM at runtime and returned with ROM/file
provenance.  No network or screenshot data is used.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os
import struct
from typing import Any

from ..dex.store import DexStore, dex_store
from ..world.gen5_rom_map import Gen5RomMap
from ..world.rom_reader import NitroRom


TRDATA_PATH = "a/0/9/1"
TRPOKE_PATH = "a/0/9/2"
MESSAGE_PATH = "a/0/0/2"
TRAINER_NAME_FILE = 382
TRAINER_CLASS_FILE = 383
MAP_SCRIPT_PATH = "a/0/5/6"
MAX_TRAINER_ID = 813

# These names come from the public Gen-V SDK / script references.  The local
# ROM parser deliberately reports them as word-level candidates until command
# widths and control-flow boundaries are verified for the current ROM.
SCRIPT_OPCODE_REFERENCES: dict[int, dict[str, str]] = {
    0x3C: {
        "name": "Message.ActorEx",
        "meaning": "actor_message_explicit_actor_candidate",
        "source": "CTRMapV SDK5-B2W2 Message.h",
    },
    0x3D: {
        "name": "Message.Actor",
        "meaning": "actor_message_parent_actor_candidate",
        "source": "CTRMapV SDK5-B2W2 Message.h",
    },
    0x48: {
        "name": "Message.ActorGendered",
        "meaning": "gendered_actor_message_candidate",
        "source": "CTRMapV SDK5-B2W2 Message.h",
    },
    0x49: {
        "name": "Message.ActorVersioned",
        "meaning": "versioned_actor_message_candidate",
        "source": "CTRMapV SDK5-B2W2 Message.h",
    },
    0x85: {
        "name": "SingleTrainerBattle",
        "meaning": "single_trainer_battle",
        "source": "PokeScriptSDK5 / current-ROM TrainerBattle scanner",
    },
    0x86: {
        "name": "DoubleTrainerBattle",
        "meaning": "double_trainer_battle",
        "source": "PokeScriptSDK5 / current-ROM TrainerBattle scanner",
    },
    0x94: {
        "name": "TrainerBattle",
        "meaning": "trainer_battle",
        "source": "PokeScriptSDK5 / current-ROM TrainerBattle scanner",
    },
}


class TrainerCatalogError(RuntimeError):
    """Raised when the ROM catalog cannot be decoded safely."""


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _render_gen5_words(words: list[int]) -> str:
    """Render the common Gen-5 text controls without interpreting variables."""
    out: list[str] = []
    index = 0
    while index < len(words):
        char = words[index]
        index += 1
        if char == 0xFFFF:
            break
        if char == 0xFFFE:
            out.append("\n")
            continue
        if char == 0xF000:
            if index + 1 >= len(words):
                out.append("{VAR}")
                break
            kind = words[index]
            count = words[index + 1]
            index += 2
            args = words[index:index + count]
            index += min(count, len(args))
            out.append("{VAR:" + ",".join(str(value) for value in [kind, *args]) + "}")
            continue
        # Gen-5 special text control values are not trainer names.  Preserve
        # them explicitly rather than silently turning them into characters.
        if char < 0x20 or char > 0xF000:
            out.append(f"\\x{char:04X}")
            continue
        out.append(chr(char))
    return "".join(out).strip()


def decode_gen5_message_file(data: bytes) -> list[list[dict[str, Any]]]:
    """Decode all language/variant blocks in one Gen-5 message member."""
    if len(data) < 12:
        raise TrainerCatalogError("Gen-5 message member is truncated")
    block_count = _u16(data, 0)
    entry_count = _u16(data, 2)
    if not 1 <= block_count <= 16 or not 1 <= entry_count <= 4096:
        raise TrainerCatalogError("Gen-5 message header is implausible")
    table_base = 0x0C
    if table_base + block_count * 4 > len(data):
        raise TrainerCatalogError("Gen-5 message block table is truncated")
    block_offsets = [_u32(data, table_base + index * 4) for index in range(block_count)]
    blocks: list[list[dict[str, Any]]] = []
    for block_index, block_offset in enumerate(block_offsets):
        if block_offset + 4 + entry_count * 8 > len(data):
            raise TrainerCatalogError(f"message block {block_index} table is outside the member")
        table: list[tuple[int, int, int]] = []
        for entry_index in range(entry_count):
            cursor = block_offset + 4 + entry_index * 8
            table.append((_u32(data, cursor), _u16(data, cursor + 4), _u16(data, cursor + 6)))
        decoded: list[dict[str, Any]] = []
        for entry_index, (relative_offset, char_count, text_flags) in enumerate(table):
            start = block_offset + relative_offset
            end = start + char_count * 2
            if start < block_offset or end > len(data):
                raise TrainerCatalogError(
                    f"message block {block_index} entry {entry_index} points outside the member"
                )
            encoded = [_u16(data, cursor) for cursor in range(start, end, 2)]
            if not encoded:
                words: list[int] = []
            else:
                key = encoded[-1] ^ 0xFFFF
                remaining = list(encoded)
                words = []
                while remaining:
                    words.insert(0, remaining.pop() ^ key)
                    key = ((key >> 3) | (key << 13)) & 0xFFFF
            decoded.append({
                "entry": entry_index,
                "text": _render_gen5_words(words),
                "text_flags": text_flags,
                "char_count": char_count,
                "raw_words": words,
            })
        blocks.append(decoded)
    return blocks


def _default_rom_path() -> str | None:
    candidates = (
        os.getenv("BLACK2_ROM_PATH"),
        r"D:\SynologyDrive\download\desmume-0.9.13-win64\口袋妖怪黑2.nds",
        r"D:\口袋妖怪黑2.nds",
        r"D:\game\desmume-0.9.13-win64\口袋妖怪黑2.nds",
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _battle_type(value: int) -> str:
    return {
        0: "single",
        1: "double",
        2: "triple",
        3: "rotation",
    }.get(value, "unresolved")


def _pokemon_summary(store: DexStore, species_id: int) -> dict[str, Any] | None:
    try:
        entity = store.get("pokemon", species_id)
    except Exception:
        entity = None
    if not isinstance(entity, dict):
        return None
    names = entity.get("names") if isinstance(entity.get("names"), dict) else {}
    return {
        "id": species_id,
        "identifier": entity.get("identifier"),
        "name": names.get("en") or entity.get("identifier"),
        "names": names,
        "source": "black2-offline-dex/v1",
    }


class TrainerRomCatalog:
    """Lazy, immutable trainer metadata reader for one Black 2 ROM."""

    def __init__(self, rom: NitroRom | None = None, rom_path: str | Path | None = None, store: DexStore | None = None):
        if rom is None:
            selected = str(rom_path) if rom_path else _default_rom_path()
            if not selected:
                raise FileNotFoundError("Black 2 ROM path is not available for TrainerRomCatalog")
            rom = NitroRom.shared(selected)
        self.rom = rom
        self.store = store or dex_store
        self._message_blocks: dict[int, list[list[dict[str, Any]]]] = {}
        self._map: Gen5RomMap | None = None
        self._zone_script_cache: dict[int, dict[str, Any]] = {}

    @property
    def rom_path(self) -> str:
        return str(self.rom.path)

    def _messages(self, file_id: int) -> list[list[dict[str, Any]]]:
        cached = self._message_blocks.get(file_id)
        if cached is not None:
            return cached
        archive = self.rom.archive(MESSAGE_PATH)
        if not 0 <= file_id < len(archive.files):
            raise TrainerCatalogError(f"message file {file_id} is outside {MESSAGE_PATH}")
        decoded = decode_gen5_message_file(archive.files[file_id])
        self._message_blocks[file_id] = decoded
        return decoded

    def _message(self, file_id: int, entry: int, block: int = 0) -> dict[str, Any] | None:
        blocks = self._messages(file_id)
        if not 0 <= block < len(blocks) or not 0 <= entry < len(blocks[block]):
            return None
        return blocks[block][entry]

    @lru_cache(maxsize=128)
    def find_trainer_ids_by_name(self, name: str, *, language_block: int = 0) -> tuple[int, ...]:
        """Return ROM trainer IDs whose decoded name exactly matches ``name``.

        This is a lookup aid for live RAM string evidence.  It does not claim
        that a matching name was the actor who caused the current battle; the
        identity decoder must still require same-frame party and causal
        evidence before exposing a candidate trainer binding.
        """
        if not isinstance(name, str) or not name:
            return ()
        if type(language_block) is not int or language_block not in (0, 1):
            raise TrainerCatalogError("language_block must be 0 or 1")
        rows = self._messages(TRAINER_NAME_FILE)[language_block]
        return tuple(
            index
            for index, row in enumerate(rows[:MAX_TRAINER_ID + 1])
            if isinstance(row, dict) and row.get("text") == name
        )

    def _trainer_variation(self, name: str, trainer_id: int, block: int) -> int:
        if not name:
            return 0
        values = self._messages(TRAINER_NAME_FILE)[block]
        return sum(1 for row in values[:trainer_id] if row.get("text") == name)

    def get(self, trainer_id: int, *, language_block: int = 0) -> dict[str, Any]:
        if type(trainer_id) is not int or not 0 <= trainer_id <= MAX_TRAINER_ID:
            raise TrainerCatalogError("trainer_id must be in 0..813 for the current B2/W2 TRData archive")
        trdata_archive = self.rom.archive(TRDATA_PATH)
        trpoke_archive = self.rom.archive(TRPOKE_PATH)
        if trainer_id >= len(trdata_archive.files) or trainer_id >= len(trpoke_archive.files):
            raise TrainerCatalogError("trainer_id is outside the current TRData/TRPoke archives")
        trdata = trdata_archive.files[trainer_id]
        trpoke = trpoke_archive.files[trainer_id]
        if len(trdata) < 20:
            raise TrainerCatalogError("TRData member is shorter than the known 20-byte header")
        if type(language_block) is not int or language_block not in (0, 1):
            raise TrainerCatalogError("language_block must be 0 or 1")
        format_flags = trdata[0]
        uses_moves = bool(format_flags & 0x01)
        uses_items = bool(format_flags & 0x02)
        trainer_class_id = trdata[1]
        battle_type_id = trdata[2]
        pokemon_count = trdata[3]
        segment_length = 8 + (2 if uses_items else 0) + (8 if uses_moves else 0)
        expected_poke_bytes = pokemon_count * segment_length
        if expected_poke_bytes > len(trpoke):
            raise TrainerCatalogError("TRPoke member is shorter than TRData party count/format requires")
        name_row = self._message(TRAINER_NAME_FILE, trainer_id, language_block)
        class_row = self._message(TRAINER_CLASS_FILE, trainer_class_id, language_block)
        name = name_row.get("text") if name_row else None
        trainer_class = class_row.get("text") if class_row else None
        party: list[dict[str, Any]] = []
        for index in range(pokemon_count):
            start = index * segment_length
            difficulty = trpoke[start]
            miscellaneous = trpoke[start + 1]
            level = trpoke[start + 2]
            species_id = _u16(trpoke, start + 4)
            form = trpoke[start + 6]
            cursor = start + 8
            held_item_id = _u16(trpoke, cursor) if uses_items else 0
            if uses_items:
                cursor += 2
            moves = [_u16(trpoke, cursor + move * 2) for move in range(4)] if uses_moves else []
            party.append({
                "slot": index + 1,
                "species_id": species_id,
                "species": _pokemon_summary(self.store, species_id),
                "form": form,
                "level": level,
                "difficulty": difficulty,
                "miscellaneous_raw": miscellaneous,
                "held_item_id": held_item_id,
                "move_ids": moves,
                "source": {"archive": TRPOKE_PATH, "member": trainer_id, "offset": start},
            })
        gym = bool(
            trainer_class_id in {10, 11, 12}
            or (trainer_class and ("道馆首领" in trainer_class or "gym" in trainer_class.casefold()))
        )
        return {
            "format": "black2-trainer-catalog/v1",
            "status": "resolved",
            "trainer_id": trainer_id,
            "name": name,
            "variation": self._trainer_variation(name or "", trainer_id, language_block),
            "trainer_class": {
                "id": trainer_class_id,
                "name": trainer_class,
                "is_gym_leader_class_candidate": gym,
            },
            "battle_type": {"id": battle_type_id, "value": _battle_type(battle_type_id)},
            "party_count": pokemon_count,
            "party": party,
            "format_flags": {"raw": format_flags, "custom_moves": uses_moves, "custom_items": uses_items},
            "ai_flags_raw": trdata[12],
            "healer": bool(trdata[16]),
            "base_money_raw": trdata[17],
            "reward_item_id": _u16(trdata, 18),
            "raw": {"trdata_hex": trdata.hex(), "trpoke_hex": trpoke.hex()},
            "provenance": {
                "rom_path": self.rom_path,
                "trdata": {"archive": TRDATA_PATH, "member": trainer_id},
                "trpoke": {"archive": TRPOKE_PATH, "member": trainer_id},
                "trainer_name": {"archive": MESSAGE_PATH, "member": TRAINER_NAME_FILE, "block": language_block, "entry": trainer_id},
                "trainer_class": {"archive": MESSAGE_PATH, "member": TRAINER_CLASS_FILE, "block": language_block, "entry": trainer_class_id},
            },
            "confidence": "rom_catalog_candidate",
        }

    @staticmethod
    def _script_starts(data: bytes) -> list[int]:
        """Read the B2/W2 script pointer table.

        The table is a sequence of little-endian relative u32 pointers and a
        trailing 0xFD13 marker.  A few old community tools read the marker
        with a two-byte look-ahead; the ROM itself is unambiguous here because
        the pointer entries are four bytes wide.  Keeping this parser local
        makes the evidence reproducible from the ROM bytes alone.
        """
        starts: list[int] = []
        cursor = 0
        while cursor + 6 <= len(data):
            relative = _u32(data, cursor)
            cursor += 4
            if relative is None:
                break
            start = cursor + relative
            if not 0 <= start < len(data):
                # A malformed table should not make a static catalog expose
                # arbitrary offsets as trainer evidence.
                break
            starts.append(start)
            if _u16(data, cursor) == 0xFD13:
                break
        return starts

    @classmethod
    def _script_function_bounds(cls, data: bytes) -> list[dict[str, Any]]:
        """Resolve pointer-table entries to bounded script-function regions.

        The pointer table is not guaranteed to be ordered by function index.
        Function boundaries therefore use the next *physical* start in sorted
        order, while the original pointer-table index remains the public
        ``script_index`` used by NPC/entity records.
        """
        starts = cls._script_starts(data)
        ordered = sorted(set(starts))
        aliases: dict[int, list[int]] = {}
        for index, start in enumerate(starts):
            aliases.setdefault(start, []).append(index)
        rows: list[dict[str, Any]] = []
        for index, start in enumerate(starts):
            try:
                physical_index = ordered.index(start)
            except ValueError:
                continue
            end = ordered[physical_index + 1] if physical_index + 1 < len(ordered) else len(data)
            end = max(start, min(int(end), len(data)))
            rows.append({
                "script_index": index,
                "start_offset": start,
                "end_offset": end,
                "length": end - start,
                "aliases": aliases.get(start, [index]),
                "alignment": "word_aligned_scan_from_even_boundary",
                "status": "bounded_candidate" if end > start else "empty_candidate",
            })
        return rows

    @classmethod
    def _script_word_candidates(
        cls,
        data: bytes,
        *,
        start: int,
        end: int,
        max_words: int = 64,
    ) -> list[dict[str, Any]]:
        """Return conservative opcode-word candidates with raw evidence.

        This is intentionally not a VM interpreter.  A numeric word can also
        occur in an operand or text argument, so every row remains
        ``candidate`` until command widths/control flow are verified.
        """
        if end <= start:
            return []
        rows: list[dict[str, Any]] = []
        origins = [("function_start", start)]
        if start % 2:
            # Most known battle-command samples are even-aligned, but the
            # current ROM also contains odd-start functions whose command
            # stream stays odd-aligned (for example Zone 427 script 11).
            origins.append(("even_boundary", start + 1))
        seen: set[tuple[int, int]] = set()
        for alignment, scan_start in origins:
            scan_end = min(end, scan_start + max(0, int(max_words)) * 2)
            for offset in range(scan_start, scan_end, 2):
                if offset + 2 > len(data):
                    break
                value = _u16(data, offset)
                reference = SCRIPT_OPCODE_REFERENCES.get(value)
                if reference is None or (offset, value) in seen:
                    continue
                seen.add((offset, value))
                rows.append({
                    "offset": offset,
                    "word": value,
                    "hex": f"0x{value:04X}",
                    "name": reference["name"],
                    "meaning": reference["meaning"],
                    "alignment": alignment,
                    "confidence": "script_word_candidate",
                    "source": reference["source"],
                    "reason": "word match only; command boundary and operand width are not decoded",
                })
        return rows

    @classmethod
    def _script_prefix_words(
        cls,
        data: bytes,
        *,
        start: int,
        end: int,
        max_words: int = 32,
    ) -> list[dict[str, Any]]:
        if end <= start:
            return []
        scan_start = start
        scan_end = min(end, scan_start + max(0, int(max_words)) * 2)
        return [
            {
                "offset": offset,
                "word": _u16(data, offset),
                "hex": f"0x{_u16(data, offset):04X}",
            }
            for offset in range(scan_start, scan_end, 2)
            if offset + 2 <= len(data)
        ]

    def zone_script_catalog(
        self,
        zone_id: int,
        *,
        script_index: int | None = None,
        prefix_words: int = 32,
        include_raw: bool = False,
        language_block: int = 0,
    ) -> dict[str, Any]:
        """Expose bounded map-script structure and entity bindings.

        This API is a reverse-engineering surface, not a claim that a script
        has executed.  It is useful for connecting a ROM NPC ``script_id`` to
        static command evidence while keeping runtime causality separate.
        """
        if type(zone_id) is not int or zone_id < 0:
            raise TrainerCatalogError("zone_id must be a non-negative integer")
        if script_index is not None and (type(script_index) is not int or script_index < 0):
            raise TrainerCatalogError("script_index must be a non-negative integer when provided")
        prefix_words = max(0, min(int(prefix_words), 256))
        rom_map = self._rom_map()
        try:
            zone = rom_map.zone(zone_id)
        except Exception as exc:
            raise TrainerCatalogError(f"zone {zone_id} is not available in the ROM map catalog: {exc}") from exc
        archive = self.rom.archive(MAP_SCRIPT_PATH)
        if not 0 <= zone.scripts_id < len(archive.files):
            raise TrainerCatalogError(f"zone {zone_id} scripts member {zone.scripts_id} is outside {MAP_SCRIPT_PATH}")
        script_bytes = archive.files[zone.scripts_id]
        functions = self._script_function_bounds(script_bytes)
        trainer_battles = self._scan_script_trainer_battles(script_bytes)
        battle_by_index: dict[int, list[dict[str, Any]]] = {}
        for row in trainer_battles:
            for index in row.get("script_indices") or []:
                battle_by_index.setdefault(int(index), []).append(dict(row))

        selected = [
            row for row in functions
            if script_index is None or row["script_index"] == script_index
        ]
        for row in selected:
            start = int(row["start_offset"])
            end = int(row["end_offset"])
            row["opcode_candidates"] = self._script_word_candidates(
                script_bytes,
                start=start,
                end=end,
                max_words=prefix_words,
            )
            row["trainer_battle_candidates"] = battle_by_index.get(row["script_index"], [])
            if include_raw or script_index is not None:
                row["raw_prefix_words"] = self._script_prefix_words(
                    script_bytes,
                    start=start,
                    end=end,
                    max_words=prefix_words,
                )

        entities = rom_map.entities(zone.entities_id)
        bindings: dict[str, list[dict[str, Any]]] = {}
        for group in ("npcs", "triggers", "furniture"):
            rows: list[dict[str, Any]] = []
            for raw in entities.get(group) or []:
                value = raw.get("script_id")
                if group == "triggers":
                    value = raw.get("reference")
                if script_index is not None and value != script_index:
                    continue
                item = {
                    key: raw.get(key)
                    for key in (
                        "record_index", "id", "sprite_id", "flag_id",
                        "script_id", "reference", "constant", "x", "y", "z",
                    )
                    if key in raw
                }
                item["binding_status"] = "static_rom_candidate"
                item["script_index"] = value
                rows.append(item)
            bindings[group] = rows

        result = {
            "format": "black2-zone-script-catalog/v1",
            "status": "candidate" if selected else "empty",
            "zone_id": zone_id,
            "scripts_id": zone.scripts_id,
            "script_length": len(script_bytes),
            "script_index": script_index,
            "functions": selected,
            "bindings": bindings,
            "trainer_battles": trainer_battles,
            "policy": {
                "execution": "static ROM only; no script was executed",
                "opcode_candidates": "word match only until command widths/control flow are verified",
                "npc_binding": "entity script_id equality is a static candidate, not runtime causality",
                "trainer_battle": "TrainerBattle rows are current-ROM scanner evidence; they do not identify the actor without a causal runtime link",
            },
            "provenance": {
                "rom_path": self.rom_path,
                "archive": MAP_SCRIPT_PATH,
                "scripts_member": zone.scripts_id,
                "entities_id": zone.entities_id,
                "language_block": language_block,
            },
        }
        if include_raw:
            result["raw"] = {
                "script_hex": script_bytes.hex(),
                "entities": entities,
            }
        return result

    @staticmethod
    def _normalize_script_trainer_id(raw_id: int) -> tuple[int | None, str]:
        """Normalize the Gen-5 map-script trainer-id encoding.

        Retail B2/W2 route scripts commonly store a trainer id with the
        0x400 script flag set (for example 0x44D -> TRData member 77).  The
        raw value remains part of every returned evidence row.
        """
        if 1 <= raw_id <= MAX_TRAINER_ID:
            return raw_id, "direct"
        flagged = raw_id - 0x400
        if 1 <= flagged <= MAX_TRAINER_ID:
            return flagged, "raw_minus_0x400"
        return None, "unresolved"

    @classmethod
    def _scan_script_trainer_battles(cls, data: bytes) -> list[dict[str, Any]]:
        """Find battle opcodes without executing or interpreting scripts."""
        starts = cls._script_starts(data)
        if not starts:
            return []
        # Functions can be listed after their callers and may point backwards;
        # scan each unique start to the next physical start boundary.
        ordered = sorted(set(starts))
        start_to_indices: dict[int, list[int]] = {}
        for index, start in enumerate(starts):
            start_to_indices.setdefault(start, []).append(index)
        rows: list[dict[str, Any]] = []
        for physical_index, start in enumerate(ordered):
            end = ordered[physical_index + 1] if physical_index + 1 < len(ordered) else len(data)
            # The pointer table can resolve a function start to an odd byte
            # offset, while the script VM still stores 16-bit opcodes on the
            # even byte boundary.  Scanning with ``start`` as the stride
            # origin therefore skips valid commands (Zone 446's TrainerBattle
            # at 0x2FA is the regression case).  Keep the raw function start
            # as evidence, but align the opcode cursor independently.
            offset = start
            while offset <= end - 4:
                opcode = _u16(data, offset)
                raw_id = None
                args = []
                step = 2
                if opcode == 0x81 and offset + 8 <= end:
                    # LeaderBattle trainerid arg2 arg3
                    args = [_u16(data, offset + 2 + 2 * index) for index in range(3)]
                    raw_id = int(args[0]) if args[0] is not None else -1
                    step = 8
                elif opcode == 0x87 and offset + 8 <= end:
                    # GymTrainerBattle trainerid arg2 arg3
                    args = [_u16(data, offset + 2 + 2 * index) for index in range(3)]
                    raw_id = int(args[0]) if args[0] is not None else -1
                    step = 8
                elif opcode == 0x85 and offset + 8 <= end:
                    # SingleTrainerBattle trainerid trainerid2 logic
                    args = [_u16(data, offset + 2 + 2 * index) for index in range(3)]
                    raw_id = int(args[0]) if args[0] is not None else -1
                    step = 8
                elif opcode == 0x86 and offset + 10 <= end:
                    # DoubleTrainerBattle ally trainerid trainerid2 logic
                    args = [_u16(data, offset + 2 + 2 * index) for index in range(4)]
                    raw_id = int(args[1]) if args[1] is not None else -1
                    step = 10
                elif opcode == 0x94 and offset + 10 <= end:
                    # TrainerBattle trainerid arg2 arg3 arg4
                    args = [_u16(data, offset + 2 + 2 * index) for index in range(4)]
                    raw_id = int(args[0]) if args[0] is not None else -1
                    step = 10
                else:
                    offset += 1
                    continue

                trainer_id, encoding = cls._normalize_script_trainer_id(raw_id)
                if trainer_id is not None:
                    rows.append({
                        "opcode": f"0x{opcode:02X}",
                        "opcode_name": {
                            0x81: "LeaderBattle",
                            0x85: "SingleTrainerBattle",
                            0x86: "DoubleTrainerBattle",
                            0x87: "GymTrainerBattle",
                            0x94: "TrainerBattle",
                        }[opcode],
                        "raw_trainer_id": raw_id,
                        "trainer_id": trainer_id,
                        "id_encoding": encoding,
                        "args": [int(value) for value in args if value is not None],
                        "offset": offset,
                        "script_start": start,
                        "script_indices": start_to_indices.get(start, []),
                        "confidence": "rom_script_candidate",
                    })
                    offset += step
                else:
                    offset += 1
        # The same function can be referenced by more than one header entry.
        # Preserve the first physical observation but avoid multiplying the
        # same static candidate in API output.
        unique: dict[tuple[int, int, int | None], dict[str, Any]] = {}
        for row in rows:
            key = (int(row["offset"]), int(row["raw_trainer_id"]), row.get("trainer_id"))
            unique.setdefault(key, row)
        return list(unique.values())

    def _rom_map(self) -> Gen5RomMap:
        if self._map is not None:
            return self._map
        if hasattr(self.rom, "zone"):
            self._map = self.rom
            return self._map
        path = getattr(self.rom, "path", None) or getattr(getattr(self.rom, "rom", None), "path", None)
        self._map = Gen5RomMap(rom_path=path)
        return self._map

    def zone_trainer_candidates(self, zone_id: int, *, language_block: int = 0) -> dict[str, Any]:
        """Return static trainer-battle candidates for one map zone.

        This is deliberately a *candidate* index.  It names trainers whose
        scripts are present in the current zone; it does not claim that a
        trainer has been engaged.  Live battle RAM and a same-session causal
        context must still match before the identity decoder raises its
        confidence.
        """
        if type(zone_id) is not int or zone_id < 0:
            raise TrainerCatalogError("zone_id must be a non-negative integer")
        cached = self._zone_script_cache.get(zone_id)
        if cached is not None:
            return cached
        rom_map = self._rom_map()
        try:
            zone = rom_map.zone(zone_id)
        except Exception as exc:
            raise TrainerCatalogError(f"zone {zone_id} is not available in the ROM map catalog: {exc}") from exc
        archive = self.rom.archive(MAP_SCRIPT_PATH)
        if not 0 <= zone.scripts_id < len(archive.files):
            raise TrainerCatalogError(f"zone {zone_id} scripts member {zone.scripts_id} is outside {MAP_SCRIPT_PATH}")
        script_bytes = archive.files[zone.scripts_id]
        battles = self._scan_script_trainer_battles(script_bytes)
        candidates: list[dict[str, Any]] = []
        seen: set[int] = set()
        for battle in battles:
            trainer_id = battle.get("trainer_id")
            if not isinstance(trainer_id, int) or trainer_id in seen:
                continue
            seen.add(trainer_id)
            row = dict(battle)
            try:
                catalog = self.get(trainer_id, language_block=language_block)
            except Exception as exc:
                row["catalog"] = None
                row["catalog_error"] = f"{type(exc).__name__}: {exc}"
            else:
                row["catalog"] = catalog
            candidates.append(row)
        result = {
            "format": "black2-zone-trainer-script-catalog/v1",
            "status": "candidate" if candidates else "empty",
            "zone_id": zone_id,
            "scripts_id": zone.scripts_id,
            "archive": MAP_SCRIPT_PATH,
            "script_length": len(script_bytes),
            "trainer_battles": battles,
            "candidates": candidates,
            "policy": "ROM script candidates only; not proof of a live encounter",
            "provenance": {
                "rom_path": self.rom_path,
                "zone_record": {"zone_id": zone_id},
                "scripts_member": zone.scripts_id,
            },
        }
        self._zone_script_cache[zone_id] = result
        return result


__all__ = [
    "MESSAGE_PATH",
    "TRAINER_CLASS_FILE",
    "TRAINER_NAME_FILE",
    "TRDATA_PATH",
    "TRPOKE_PATH",
    "MAP_SCRIPT_PATH",
    "MAX_TRAINER_ID",
    "TrainerCatalogError",
    "TrainerRomCatalog",
    "decode_gen5_message_file",
]
