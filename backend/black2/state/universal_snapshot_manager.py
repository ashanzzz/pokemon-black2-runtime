"""Universal ground-truth snapshot manager for Pokémon Black 2 (IREJ).

A snapshot is a portable reverse-engineering artifact, not merely metadata
about an attempted capture. Each physical capture records the real 4 MiB Main
RAM image, native screenshot, registers, semantic state, same-dump forensic
extracts, GFL heap candidates, checksums, and a downloadable ZIP bundle.

The semantic state is sampled immediately before the physical dump and is
explicitly frame-stamped separately. Physical claims should use the bridge dump
frame and/or ``main_ram.bin``.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..world.runtime_field_resolver import resolve_runtime_field_from_ram


SNAPSHOT_BASE_DIR = Path("reverse_engineering/dumps").resolve()
MAIN_RAM_SIZE = 0x400000
ARM9_MAIN_RAM_BASE = 0x02000000

# These are the NDS memory domains exposed by the project BizHawk bridge.  A
# universal capture is only complete when every one was written and its bytes
# on disk match the requested size.  The raw files are the evidence; any JSON
# derived below is an index for an AI, never a replacement for the dump.
DUMP_DOMAINS = (
    ("Main RAM", "main_ram.bin", 0x400000),
    ("Instruction TCM", "itcm.bin", 0x8000),
    ("Data TCM", "dtcm.bin", 0x4000),
    ("Shared WRAM", "shared_wram.bin", 0x8000),
    ("ARM7 WRAM", "arm7_wram.bin", 0x10000),
    ("SRAM", "sram.bin", 0x80000),
)

_DOMAIN_FILE_NAMES = {
    "Main RAM": "main_ram.bin",
    "Instruction TCM": "itcm.bin",
    "Data TCM": "dtcm.bin",
    "Shared WRAM": "shared_wram.bin",
    "ARM7 WRAM": "arm7_wram.bin",
    "SRAM": "sram.bin",
    "ARM9 BIOS": "arm9_bios.bin",
    "ARM7 BIOS": "arm7_bios.bin",
    "Firmware": "firmware.bin",
    "Waterbox PageData": "waterbox_pagedata.bin",
}
_EXCLUDED_DOMAIN_NAMES = {"ROM"}

# These mirrors are an evidence *candidate* for Map Section ID.  They are not
# a verified Field/MapMtxSys pointer chain, so the export keeps both raw votes
# and the lower confidence level instead of asserting a current map resource.
MAP_ID_OFFSETS = (0x1434A6, 0x143668, 0x146CE8, 0x146EB2)

# Convenience extracts taken from main_ram.bin itself. main_ram.bin remains the
# authoritative source and allows discovery outside every known window.
FORENSIC_RANGES = [
    ("scriptwork_context", 0x247400, 0x800),
    ("message_region", 0x249000, 0x2000),
    ("legacy_printer_neighborhood", 0x31F000, 0x3000),
    ("field_heap_neighborhood", 0x32B000, 0x4000),
    ("tcbl_neighborhood", 0x332A00, 0x1000),
    ("bitmap_neighborhood", 0x335000, 0x3000),
]

DIALOGUE_HEAP_TAG_HINTS = (
    "tcbl",
    "bmp",
    "strbuf",
    "word",
    "talk",
    "msg",
    "font",
    "win",
)


@dataclass
class PlayerContext:
    verified: bool = False
    grid_x: Optional[int] = None
    grid_y: Optional[int] = None
    elevation_z: Optional[int] = None
    world_fx_x: Optional[int] = None
    world_fx_y: Optional[int] = None
    facing: str = "Unknown"
    movement_state: str = "Idle"
    ex_posture: str = "Walk/Run"


@dataclass
class ActorSlot:
    slot_id: int
    uid: int
    scrid: int
    model_id: int
    grid_pos: Dict[str, int]
    facing: int
    raw_addr: str


@dataclass
class MapContext:
    map_section_id: Optional[int] = None
    location_name: str = "未知地点"
    matrix_id: Optional[int] = None
    chunk_index: Optional[Dict[str, int]] = None


@dataclass
class DialogueContext:
    active: bool = False
    hardware_lock: bool = False
    speaker: str = "无"
    speaker_category: str = "IDLE"
    visible_lines: List[str] = field(default_factory=list)
    raw_loaded_stream: str = ""
    printer_phase: Optional[int] = None
    source_cursor: Optional[str] = None
    confidence: str = "unresolved"


@dataclass
class SnapshotManifest:
    schema_version: str = "universal_snapshot/v2"
    snapshot_id: str = ""
    timestamp_utc: str = ""
    frame: int = 0
    semantic_state_frame: int = 0
    frame_delta: int = 0
    category: str = "OVERWORLD_EXPLORE"
    label: str = ""
    operator_notes: str = ""
    rom_hash: str = "8DB71663502BBF3B43AC3C9052EC390C390BE62F"
    capture_complete: bool = False
    capture_errors: List[str] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    registers: Dict[str, Any] = field(default_factory=dict)
    player: PlayerContext = field(default_factory=PlayerContext)
    actors: List[ActorSlot] = field(default_factory=list)
    map: MapContext = field(default_factory=MapContext)
    dialogue: DialogueContext = field(default_factory=DialogueContext)
    raw_state_dump: Dict[str, Any] = field(default_factory=dict)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_meta(path: Path) -> Dict[str, Any]:
    exists = path.exists() and path.is_file()
    size = path.stat().st_size if exists else 0
    return {
        "exists": exists,
        "size": size,
        "sha256": _sha256_file(path) if exists else None,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_forensic_ranges(ram: bytes, frame: int) -> Dict[str, Any]:
    ranges: Dict[str, Any] = {}
    for range_id, offset, length in FORENSIC_RANGES:
        if offset < 0 or offset >= len(ram):
            continue
        end = min(len(ram), offset + length)
        chunk = ram[offset:end]
        ranges[range_id] = {
            "domain": "Main RAM",
            "frame": frame,
            "offset": f"0x{offset:06X}",
            "address": f"0x{ARM9_MAIN_RAM_BASE + offset:08X}",
            "length": len(chunk),
            "sha256": _sha256_bytes(chunk),
            "hex": chunk.hex(),
        }
    return {
        "schema": "pokemon_black2_forensic_ranges/v1",
        "source": "main_ram.bin; extracted after capture without another emulator read",
        "physical_dump_frame": frame,
        "ranges": ranges,
    }


def _domain_file_name(name: str) -> str:
    known = _DOMAIN_FILE_NAMES.get(name)
    if known:
        return known
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "unnamed"
    return f"domain_{slug}.bin"


async def _resolve_dump_domains(transport: Any) -> tuple[tuple[tuple[str, str, int], ...], Dict[str, Any]]:
    """Choose every readable, non-ROM memory domain reported by this BizHawk."""
    reported: Any = {}
    try:
        reported = await transport.request("memory.domains")
    except Exception as exc:
        # The six memory domains are the verified minimum.  A failed inventory
        # is recorded rather than quietly claiming an exhaustive export.
        return DUMP_DOMAINS, {
            "schema": "pokemon_black2_memory_inventory/v1",
            "inventory_status": "fallback",
            "reason": f"memory.domains failed: {type(exc).__name__}: {exc}",
            "excluded": [],
        }

    if not isinstance(reported, dict):
        reported = {}
    selected: list[tuple[str, str, int]] = []
    excluded: list[Dict[str, Any]] = []
    for name, metadata in reported.items():
        if not isinstance(name, str) or not isinstance(metadata, dict):
            continue
        size = metadata.get("size", 0)
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 0
        if name in _EXCLUDED_DOMAIN_NAMES:
            excluded.append({"name": name, "size": size, "reason": "ROM/game file is explicitly excluded"})
        elif not metadata.get("readable", True):
            excluded.append({"name": name, "size": size, "reason": "bridge marks domain unreadable"})
        elif size <= 0:
            excluded.append({"name": name, "size": size, "reason": "virtual bus alias has no independent byte range"})
        else:
            selected.append((name, _domain_file_name(name), size))

    # A malformed/empty inventory must not turn into a successful zero-domain
    # capture.  The known ARM9/ARM7 RAM set remains the conservative fallback.
    if not selected:
        return DUMP_DOMAINS, {
            "schema": "pokemon_black2_memory_inventory/v1",
            "inventory_status": "fallback",
            "reason": "bridge returned no readable non-ROM domains",
            "reported_domains": reported,
            "excluded": excluded,
        }
    selected.sort(key=lambda item: (item[0] != "Main RAM", item[0]))
    return tuple(selected), {
        "schema": "pokemon_black2_memory_inventory/v1",
        "inventory_status": "verified",
        "source": "BizHawk memory.domains immediately before memory.dump_universal",
        "reported_domains": reported,
        "excluded": excluded,
    }


def _domain_evidence(
    target_folder: Path, bridge_domains: Any, frame: int, dump_domains: tuple[tuple[str, str, int], ...]
) -> Dict[str, Any]:
    """Validate every requested domain against bytes actually written locally."""
    bridge_domains = bridge_domains if isinstance(bridge_domains, dict) else {}
    records = []
    for name, file_name, expected_size in dump_domains:
        path = target_folder / file_name
        actual_size = path.stat().st_size if path.exists() and path.is_file() else 0
        bridge = bridge_domains.get(name, {})
        bridge_size = bridge.get("size") if isinstance(bridge, dict) else None
        bridge_success = bool(bridge.get("success")) if isinstance(bridge, dict) else False
        complete = actual_size == expected_size and bridge_size == expected_size and bridge_success
        records.append({
            "name": name,
            "file": file_name,
            "expected_size": expected_size,
            "actual_size": actual_size,
            "bridge_size": bridge_size,
            "bridge_success": bridge_success,
            "complete": complete,
            "sha256": _sha256_file(path) if actual_size == expected_size else None,
        })
    return {
        "schema": "pokemon_black2_memory_domains/v1",
        "physical_dump_frame": frame,
        "source": "BizHawk memory.dump_universal; sizes rechecked from local files",
        "complete": all(record["complete"] for record in records),
        "domains": records,
    }


def _map_identity_from_dump(ram: bytes, frame: int) -> Dict[str, Any]:
    """Expose raw map-section mirror votes without upgrading them to Field data."""
    votes: Dict[int, int] = {}
    observations = []
    for offset in MAP_ID_OFFSETS:
        raw = ram[offset:offset + 2]
        value = int.from_bytes(raw, "little") if len(raw) == 2 else None
        plausible = value is not None and 0 < value < 4096
        if plausible:
            votes[value] = votes.get(value, 0) + 1
        observations.append({
            "domain": "Main RAM",
            "offset": f"0x{offset:06X}",
            "address": f"0x{ARM9_MAIN_RAM_BASE + offset:08X}",
            "u16_le": value,
            "plausible": plausible,
        })
    winner = max(votes, key=votes.get) if votes else None
    matching = votes.get(winner, 0) if winner is not None else 0
    return {
        "value": winner if matching >= 2 else None,
        "confidence": "candidate" if matching >= 2 else "unresolved",
        "physical_dump_frame": frame,
        "votes": observations,
        "reason": (
            "two or more RAM mirrors agree; Field/MapMtxSys pointer-chain validation is still required"
            if matching >= 2 else "no repeatable map-section mirror agreement in this physical dump"
        ),
    }


def _runtime_world_index(ram: bytes, frame: int, semantic_state: Any, semantic_frame: int) -> Dict[str, Any]:
    """Machine-readable hand-off contract for AI analysis of one physical dump.

    This deliberately does not turn static NPC spawns, dialogue guesses, or
    legacy coordinate mirrors into runtime actors.  Those fields stay
    unresolved until the FieldActor resolver has direct RAM evidence.
    """
    verified_position = bool(getattr(semantic_state, "player_position_verified", False))
    position = getattr(semantic_state, "player_world_pos", {}) or {}
    return {
        "schema": "pokemon_black2_runtime_world_export/v1",
        "physical_dump_frame": frame,
        "semantic_state_frame": semantic_frame,
        "frame_delta": frame - semantic_frame,
        "authority": {
            "dynamic_facts": "main_ram.bin and the other raw memory-domain files at physical_dump_frame",
            "semantic_context": "semantic_state.json sampled before the physical dump; never treat it as same-frame raw memory",
            "static_resources": "not joined into this export until the current resource is confirmed from runtime RAM",
        },
        "player": {
            "actor": {"value": None, "confidence": "unresolved", "reason": "FieldPlayerCore -> PlayerActor chain is not verified"},
            "world_position": {
                "value": position if verified_position else None,
                "confidence": "verified" if verified_position else "unresolved",
                "source": "semantic_state.json" if verified_position else "no verified FieldActor position in this dump",
            },
            "facing": {"value": getattr(semantic_state, "player_facing", None) if verified_position else None, "confidence": "verified" if verified_position else "unresolved"},
            "movement": {"value": getattr(semantic_state, "movement_state", None) if verified_position else None, "confidence": "verified" if verified_position else "unresolved"},
        },
        "map": {"map_section_id": _map_identity_from_dump(ram, frame), "matrix_id": {"value": None, "confidence": "unresolved"}, "loaded_chunks": {"value": [], "confidence": "unresolved"}},
        "actors": {
            "count": {"value": None, "confidence": "unresolved"},
            "runtime_actors": [],
            "npc_names": {"value": [], "confidence": "unresolved", "reason": "NPC names require a verified runtime actor/script/text binding"},
            "reason": "ActorHeap layout and FieldActor stride have not yet been verified for this ROM/session",
        },
        "interactions": {"warps": [], "triggers": [], "objects": [], "confidence": "unresolved"},
        "unresolved_runtime_subsystems": {
            "trainer_and_party": "raw bytes are included in memory domains; a save/runtime decoder is not verified",
            "battle": "raw bytes are included in memory domains; battle object resolver is not verified",
            "camera": "raw bytes are included in memory domains; FieldCamera resolver is not verified",
            "terrain_and_collision": "raw bytes are included in memory domains; current terrain resolver is not verified",
        },
    }


def _scan_gfl_heap_candidates(ram: bytes, frame: int) -> Dict[str, Any]:
    """Record GFL-style heap headers as candidates without promoting semantics."""
    magic = b"\x44\x55\x00\x00"
    candidates: List[Dict[str, Any]] = []
    dialogue_candidates: List[Dict[str, Any]] = []
    cursor = 0

    while True:
        offset = ram.find(magic, cursor)
        if offset < 0:
            break
        cursor = offset + 4
        if offset + 0x20 > len(ram):
            continue

        block_size = int.from_bytes(ram[offset + 0x04 : offset + 0x08], "little")
        prev_ptr = int.from_bytes(ram[offset + 0x08 : offset + 0x0C], "little")
        next_ptr = int.from_bytes(ram[offset + 0x0C : offset + 0x10], "little")
        heap_flags = int.from_bytes(ram[offset + 0x10 : offset + 0x14], "little")
        raw_tag = ram[offset + 0x14 : offset + 0x1C]
        source_tag = raw_tag.split(b"\x00", 1)[0].decode("ascii", errors="replace")
        line_number = int.from_bytes(ram[offset + 0x1C : offset + 0x1E], "little")
        reserved = int.from_bytes(ram[offset + 0x1E : offset + 0x20], "little")
        payload_offset = offset + 0x20
        preview_end = min(len(ram), payload_offset + 0x80)

        printable_tag = bool(source_tag) and all(32 <= b < 127 for b in raw_tag if b != 0)
        if not printable_tag:
            continue

        record = {
            "header_offset": f"0x{offset:06X}",
            "header_address": f"0x{ARM9_MAIN_RAM_BASE + offset:08X}",
            "block_size_field": block_size,
            "prev_ptr": f"0x{prev_ptr:08X}",
            "next_ptr": f"0x{next_ptr:08X}",
            "heap_flags": f"0x{heap_flags:08X}",
            "heap_id_low16": heap_flags & 0xFFFF,
            "source_tag": source_tag,
            "line_number": line_number,
            "reserved": reserved,
            "candidate_payload_offset": f"0x{payload_offset:06X}",
            "candidate_payload_address": f"0x{ARM9_MAIN_RAM_BASE + payload_offset:08X}",
            "header_hex": ram[offset : offset + 0x20].hex(),
            "payload_preview_hex": ram[payload_offset:preview_end].hex(),
            "confidence": "candidate",
        }
        candidates.append(record)
        lowered = source_tag.lower()
        if any(hint in lowered for hint in DIALOGUE_HEAP_TAG_HINTS):
            dialogue_candidates.append(record)

        if len(candidates) >= 4096:
            break

    return {
        "schema": "gfl_heap_candidate_scan/v1",
        "physical_dump_frame": frame,
        "method": "scan main_ram.bin for 0x00005544 headers; no RAM writes",
        "interpretation": "candidate only; active ownership must be validated from pointers/lifecycle",
        "candidate_count": len(candidates),
        "dialogue_candidate_count": len(dialogue_candidates),
        "dialogue_candidates": dialogue_candidates,
        "all_candidates": candidates,
    }


def _build_bundle(target_folder: Path, snapshot_id: str, file_names: List[str]) -> Path:
    bundle_path = target_folder / f"{snapshot_id}.zip"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file_name in file_names:
            path = target_folder / file_name
            if path.exists() and path.is_file():
                archive.write(path, arcname=file_name)
    return bundle_path


def _verify_bundle(bundle_path: Path, integrity_path: Path) -> Dict[str, Any]:
    """Verify the downloadable archive, not merely its source directory."""
    if not bundle_path.exists() or not bundle_path.is_file():
        return {"ok": False, "reason": "ZIP file is missing"}
    if not zipfile.is_zipfile(bundle_path):
        return {"ok": False, "reason": "bundle is not a readable ZIP"}
    try:
        integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
        artifacts = integrity.get("artifacts", {})
        expected = set(artifacts) | {"integrity.json"}
        with zipfile.ZipFile(bundle_path) as archive:
            names = set(archive.namelist())
            corrupt_member = archive.testzip()
            missing = sorted(expected - names)
            mismatched = []
            for file_name, metadata in artifacts.items():
                expected_hash = metadata.get("sha256") if isinstance(metadata, dict) else None
                if expected_hash and file_name in names:
                    if _sha256_bytes(archive.read(file_name)) != expected_hash:
                        mismatched.append(file_name)
        if corrupt_member:
            return {"ok": False, "reason": f"ZIP CRC failure: {corrupt_member}"}
        if missing:
            return {"ok": False, "reason": "ZIP missing expected members", "missing": missing}
        if mismatched:
            return {"ok": False, "reason": "ZIP member hash mismatch", "mismatched": mismatched}
        if "screen.png" not in names:
            return {"ok": False, "reason": "ZIP is missing screen.png"}
        return {
            "ok": True,
            "member_count": len(names),
            "verified_members": sorted(expected),
            "sha256": _sha256_file(bundle_path),
        }
    except (OSError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        return {"ok": False, "reason": f"ZIP verification failed: {type(exc).__name__}: {exc}"}


class UniversalSnapshotManager:
    """Create, validate, package, and list portable full-RAM snapshots."""

    def __init__(self, base_dir: Path = SNAPSHOT_BASE_DIR):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def create_snapshot(
        self,
        transport: Any,
        state_engine: Any,
        category: str = "OVERWORLD_EXPLORE",
        label: str = "unlabelled",
        notes: str = "",
    ) -> Dict[str, Any]:
        capture_errors: List[str] = []
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        # Useful context, but not silently promoted to same-frame physical evidence.
        curr_state = await state_engine.sample_once()
        semantic_frame = int(curr_state.frame or transport.last_frame or 0)

        clean_label = re.sub(r"[^\w\-_]", "_", label.strip() or "sample")
        folder_name = f"dump_{timestamp_str}_f{semantic_frame}_{category}_{clean_label}"
        target_folder = self.base_dir / folder_name
        target_folder.mkdir(parents=True, exist_ok=False)

        bin_path = target_folder / "main_ram.bin"
        png_path = target_folder / "screen.png"
        # BizHawk's embedded Lua file APIs can fail on a Unicode workspace
        # path.  Let the bridge write to an ASCII-only temporary folder, then
        # move the resulting raw files into the user-selected evidence folder
        # before any validation or derived analysis occurs.
        bridge_stage = Path(tempfile.mkdtemp(prefix="black2_dump_"))

        dump_domains, domain_inventory = await _resolve_dump_domains(transport)
        domains_spec = [
            {"name": name, "file": file_name, "size": expected_size}
            for name, file_name, expected_size in dump_domains
        ]

        # RAM + screenshot + registers are captured by one bridge operation.
        try:
            bridge_res = await transport.request(
                "memory.dump_universal",
                {
                    "dump_dir": str(bridge_stage).replace("\\", "/"),
                    "bin_path": str(bridge_stage / "main_ram.bin").replace("\\", "/"),
                    "png_path": str(bridge_stage / "screen.png").replace("\\", "/"),
                    "domain": "Main RAM",
                    "size": MAIN_RAM_SIZE,
                    "domains": domains_spec,
                },
            )
            for _name, file_name, _expected_size in dump_domains:
                staged_file = bridge_stage / file_name
                if staged_file.exists() and staged_file.is_file():
                    shutil.move(str(staged_file), str(target_folder / file_name))
            staged_screen = bridge_stage / "screen.png"
            if staged_screen.exists() and staged_screen.is_file():
                shutil.move(str(staged_screen), str(png_path))
        finally:
            shutil.rmtree(bridge_stage, ignore_errors=True)
        physical_frame = int(bridge_res.get("frame") or semantic_frame or 0)
        raw_regs = bridge_res.get("registers", {}) or {}

        domains_payload = bridge_res.get("domains", {})
        domain_inventory["physical_dump_frame"] = physical_frame
        domain_inventory["exported_domains"] = [
            {"name": name, "file": file_name, "expected_size": expected_size}
            for name, file_name, expected_size in dump_domains
        ]
        memory_domains = _domain_evidence(target_folder, domains_payload, physical_frame, dump_domains)
        main_domain = next(item for item in memory_domains["domains"] if item["name"] == "Main RAM")
        actual_ram_bytes = int(main_domain["actual_size"])
        bridge_written_bytes = int(main_domain["bridge_size"] or 0)
        ram_ok = bool(main_domain["complete"])
        screen_ok = (
            bool(bridge_res.get("screenshot_saved", False))
            and png_path.exists()
            and png_path.stat().st_size > 0
        )

        for domain in memory_domains["domains"]:
            if not domain["complete"]:
                capture_errors.append(
                    f"{domain['file']} incomplete: bridge={domain['bridge_size']}, file={domain['actual_size']}, expected={domain['expected_size']}"
                )
        if not screen_ok:
            capture_errors.append("screen.png was not confirmed by the BizHawk bridge")

        ctx = curr_state.context
        printer = ctx.printer or {}
        wpos = curr_state.player_world_pos or {}
        p_ctx = PlayerContext(
            verified=bool(curr_state.player_position_verified),
            grid_x=wpos.get("x"),
            grid_y=wpos.get("y"),
            elevation_z=wpos.get("z"),
            facing=curr_state.player_facing or "Unresolved",
            movement_state=curr_state.movement_state or "Unresolved",
        )
        m_ctx = MapContext(
            map_section_id=curr_state.map_section_id,
            location_name=curr_state.location or "未知区域",
        )
        d_ctx = DialogueContext(
            active=bool(ctx.is_dialogue_active),
            hardware_lock=bool(ctx.is_dialogue_active),
            speaker=ctx.speaker or "无",
            speaker_category=ctx.speaker_category or "IDLE",
            visible_lines=printer.get("lines", []),
            raw_loaded_stream=ctx.loaded_dialogue_text or "",
            printer_phase=printer.get("candidate_control_phase"),
            source_cursor=printer.get("candidate_continuation_cursor"),
            confidence=printer.get("visible_text_confidence", "unresolved"),
        )

        semantic_path = target_folder / "semantic_state.json"
        registers_path = target_folder / "registers.json"
        critical_path = target_folder / "critical_ranges.json"
        heap_path = target_folder / "heap_candidates.json"
        domains_path = target_folder / "memory_domains.json"
        inventory_path = target_folder / "memory_domain_inventory.json"
        runtime_world_path = target_folder / "runtime_world.json"
        metadata_path = target_folder / "metadata.json"
        manifest_path = target_folder / "manifest.json"
        integrity_path = target_folder / "integrity.json"

        _write_json(
            semantic_path,
            {
                "schema": "semantic_state_context/v1",
                "semantic_state_frame": semantic_frame,
                "physical_dump_frame": physical_frame,
                "frame_delta": physical_frame - semantic_frame,
                "warning": "semantic state was sampled immediately before physical dump; use main_ram.bin for same-frame physical claims",
                "state": curr_state.model_dump(),
            },
        )
        _write_json(registers_path, {"frame": physical_frame, "registers": raw_regs})

        if ram_ok:
            ram = bin_path.read_bytes()
            _write_json(critical_path, _extract_forensic_ranges(ram, physical_frame))
            _write_json(heap_path, _scan_gfl_heap_candidates(ram, physical_frame))
            runtime_index = _runtime_world_index(ram, physical_frame, curr_state, semantic_frame)
            runtime_index["field_runtime"] = resolve_runtime_field_from_ram(ram, frame=physical_frame)
            _write_json(runtime_world_path, runtime_index)
        else:
            _write_json(
                critical_path,
                {
                    "schema": "pokemon_black2_forensic_ranges/v1",
                    "physical_dump_frame": physical_frame,
                    "error": "main_ram.bin is missing or incomplete; no derived ranges were generated",
                    "ranges": {},
                },
            )
            _write_json(
                heap_path,
                {
                    "schema": "gfl_heap_candidate_scan/v1",
                    "physical_dump_frame": physical_frame,
                    "error": "main_ram.bin is missing or incomplete; no heap candidates were scanned",
                    "candidate_count": 0,
                    "dialogue_candidate_count": 0,
                    "dialogue_candidates": [],
                    "all_candidates": [],
                },
            )
            _write_json(
                runtime_world_path,
                {
                    "schema": "pokemon_black2_runtime_world_export/v1",
                    "physical_dump_frame": physical_frame,
                    "semantic_state_frame": semantic_frame,
                    "error": "main_ram.bin is missing or incomplete; runtime index was not derived",
                },
            )

        _write_json(domains_path, memory_domains)
        _write_json(inventory_path, domain_inventory)

        _write_json(
            metadata_path,
            {
                "label": label,
                "category": category,
                "timestamp": timestamp_str,
                "frame": physical_frame,
                "semantic_state_frame": semantic_frame,
                "semantic_state": curr_state.model_dump(),
            },
        )

        capture_complete = bool(memory_domains["complete"]) and screen_ok
        files: Dict[str, str] = {}
        for key, path in (
            ("main_ram_bin", bin_path),
            ("screen_png", png_path),
            ("semantic_state_json", semantic_path),
            ("registers_json", registers_path),
            ("critical_ranges_json", critical_path),
            ("heap_candidates_json", heap_path),
            ("memory_domains_json", domains_path),
            ("memory_domain_inventory_json", inventory_path),
            ("runtime_world_json", runtime_world_path),
            ("metadata_json", metadata_path),
        ):
            if path.exists():
                files[key] = path.name
        for domain_name, file_name, _expected_size in dump_domains:
            domain_file = target_folder / file_name
            if domain_file.exists():
                files[f"memory_domain_{_domain_file_name(domain_name).removesuffix('.bin')}"] = file_name

        artifact_paths = [
            *(target_folder / file_name for _name, file_name, _size in dump_domains),
            png_path,
            semantic_path,
            registers_path,
            critical_path,
            heap_path,
            domains_path,
            inventory_path,
            runtime_world_path,
            metadata_path,
        ]
        artifact_summary = {path.name: _artifact_meta(path) for path in artifact_paths}

        manifest = SnapshotManifest(
            snapshot_id=folder_name,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            frame=physical_frame,
            semantic_state_frame=semantic_frame,
            frame_delta=physical_frame - semantic_frame,
            category=category,
            label=label,
            operator_notes=notes,
            capture_complete=capture_complete,
            capture_errors=capture_errors,
            files=files,
            artifacts=artifact_summary,
            registers=raw_regs,
            player=p_ctx,
            actors=[],  # Runtime ActorHeap has not been validated; see runtime_world.json.
            map=m_ctx,
            dialogue=d_ctx,
            raw_state_dump=curr_state.model_dump(),
        )
        _write_json(manifest_path, asdict(manifest))

        pre_bundle_files = [
            *(file_name for _name, file_name, _size in dump_domains),
            "screen.png",
            "manifest.json",
            "metadata.json",
            "semantic_state.json",
            "registers.json",
            "critical_ranges.json",
            "heap_candidates.json",
            "memory_domains.json",
            "memory_domain_inventory.json",
            "runtime_world.json",
        ]
        integrity_payload = {
            "schema": "snapshot_integrity/v1",
            "snapshot_id": folder_name,
            "physical_dump_frame": physical_frame,
            "expected_main_ram_bytes": MAIN_RAM_SIZE,
            "capture_complete": capture_complete,
            "capture_errors": capture_errors,
            "memory_domains": memory_domains["domains"],
            "memory_domain_inventory": domain_inventory,
            "artifacts": {
                file_name: _artifact_meta(target_folder / file_name)
                for file_name in pre_bundle_files
            },
        }
        _write_json(integrity_path, integrity_payload)

        bundle_files = pre_bundle_files + ["integrity.json"]
        bundle_path = _build_bundle(target_folder, folder_name, bundle_files)
        bundle_meta = _artifact_meta(bundle_path)
        bundle_verification = _verify_bundle(bundle_path, integrity_path)

        return {
            "ok": True,
            "complete": capture_complete,
            "snapshot_id": folder_name,
            "folder": str(target_folder),
            "frame": physical_frame,
            "semantic_state_frame": semantic_frame,
            "frame_delta": physical_frame - semantic_frame,
            "category": category,
            "label": label,
            "expected_main_ram_bytes": MAIN_RAM_SIZE,
            "main_ram_size": actual_ram_bytes,
            "bridge_written_bytes": bridge_written_bytes,
            "screenshot_saved": screen_ok,
            "registers_count": len(raw_regs),
            "exported_domain_count": len(dump_domains),
            "capture_errors": capture_errors,
            "memory_domains": memory_domains["domains"],
            "artifacts": integrity_payload["artifacts"],
            "bundle": {
                **bundle_meta,
                "file_name": bundle_path.name,
                "url": f"/api/dev/dumps/{folder_name}/download",
                "verification": bundle_verification,
            },
            "dialogue_text": d_ctx.visible_lines,
        }

    def list_snapshots(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List snapshots using actual disk artifacts, never manifest claims alone."""
        if not self.base_dir.exists():
            return []

        entries: List[Dict[str, Any]] = []
        for directory in sorted(self.base_dir.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            manifest_path = directory / "manifest.json"
            metadata_path = directory / "metadata.json"
            if not manifest_path.exists() and not metadata_path.exists():
                continue

            try:
                if manifest_path.exists():
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                else:
                    data = json.loads(metadata_path.read_text(encoding="utf-8"))

                bin_path = directory / "main_ram.bin"
                png_path = directory / "screen.png"
                integrity_path = directory / "integrity.json"
                runtime_world_path = directory / "runtime_world.json"
                bundle_candidates = list(directory.glob("*.zip"))
                bundle_path = bundle_candidates[0] if bundle_candidates else None
                bundle_verification = (
                    _verify_bundle(bundle_path, integrity_path)
                    if bundle_path and integrity_path.exists()
                    else {"ok": False, "reason": "ZIP or integrity manifest is missing"}
                )

                ram_size = bin_path.stat().st_size if bin_path.exists() else 0
                png_size = png_path.stat().st_size if png_path.exists() else 0
                domain_records = []
                if integrity_path.exists():
                    try:
                        domain_records = json.loads(integrity_path.read_text(encoding="utf-8")).get("memory_domains", [])
                    except (OSError, json.JSONDecodeError):
                        domain_records = []
                actual_complete = (
                    ram_size == MAIN_RAM_SIZE
                    and png_size > 0
                    and bool(domain_records)
                    and all(bool(record.get("complete")) for record in domain_records)
                )
                dialogue = data.get("dialogue", {})
                registers = data.get("registers", {})

                entries.append(
                    {
                        "folder_name": directory.name,
                        "category": data.get("category", "OVERWORLD_EXPLORE"),
                        "label": data.get("label", directory.name),
                        "timestamp": data.get("timestamp_utc") or data.get("timestamp", ""),
                        "frame": data.get("frame", 0),
                        "semantic_state_frame": data.get("semantic_state_frame"),
                        "frame_delta": data.get("frame_delta"),
                        "complete": actual_complete,
                        "capture_errors": data.get("capture_errors", []),
                        "has_bin": bin_path.exists(),
                        "ram_size": ram_size,
                        "has_png": png_path.exists(),
                        "png_size": png_size,
                        "has_integrity": integrity_path.exists(),
                        "has_bundle": bool(bundle_path and bundle_path.exists()),
                        "bundle_verified": bool(bundle_verification.get("ok")),
                        "bundle_verification": bundle_verification,
                        "bin_url": f"/dumps/{directory.name}/main_ram.bin" if bin_path.exists() else None,
                        "png_url": f"/dumps/{directory.name}/screen.png" if png_path.exists() else None,
                        "manifest_url": f"/dumps/{directory.name}/manifest.json" if manifest_path.exists() else None,
                        "integrity_url": f"/dumps/{directory.name}/integrity.json" if integrity_path.exists() else None,
                        "world_url": f"/dumps/{directory.name}/runtime_world.json" if runtime_world_path.exists() else None,
                        "bundle_url": f"/api/dev/dumps/{directory.name}/download" if bundle_verification.get("ok") else None,
                        "player": data.get("player", {}),
                        "map": data.get("map", {}),
                        "dialogue": dialogue,
                        "registers_count": len(registers),
                        "memory_domains": domain_records,
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue

        return entries[:limit]

    def verified_bundle_path(self, snapshot_id: str) -> tuple[Path | None, Dict[str, Any]]:
        """Return a ZIP only after validating its members and hashes."""
        if not re.fullmatch(r"dump_[A-Za-z0-9_\-]+", snapshot_id):
            return None, {"ok": False, "reason": "invalid snapshot id"}
        directory = (self.base_dir / snapshot_id).resolve()
        try:
            directory.relative_to(self.base_dir.resolve())
        except ValueError:
            return None, {"ok": False, "reason": "snapshot path escapes dump directory"}
        candidates = sorted(directory.glob("*.zip")) if directory.is_dir() else []
        integrity_path = directory / "integrity.json"
        if not candidates:
            return None, {"ok": False, "reason": "ZIP file is missing"}
        verification = _verify_bundle(candidates[0], integrity_path)
        return (candidates[0] if verification.get("ok") else None), verification

    def clear_snapshots(self) -> Dict[str, Any]:
        """Delete only generated snapshot folders and the legacy root bundle."""
        deleted, deleted_bytes = [], 0
        base = self.base_dir.resolve()
        if not base.exists():
            return {"ok": True, "deleted": deleted, "deleted_bytes": deleted_bytes}
        for path in base.iterdir():
            if path.is_dir() and path.name.startswith("dump_"):
                deleted_bytes += sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
                shutil.rmtree(path)
                deleted.append(path.name)
            elif path.is_file() and path.name == "dumps.zip":
                deleted_bytes += path.stat().st_size
                path.unlink()
                deleted.append(path.name)
        return {"ok": True, "deleted": deleted, "deleted_count": len(deleted), "deleted_bytes": deleted_bytes}


universal_snapshot_manager = UniversalSnapshotManager()
