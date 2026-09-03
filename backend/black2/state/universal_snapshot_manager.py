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
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


SNAPSHOT_BASE_DIR = Path("reverse_engineering/dumps").resolve()
MAIN_RAM_SIZE = 0x400000
ARM9_MAIN_RAM_BASE = 0x02000000

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

        # Domains to dump
        domains_spec = [
            {"name": "Main RAM", "file": "main_ram.bin", "size": 0x400000},
            {"name": "Instruction TCM", "file": "itcm.bin", "size": 0x8000},
            {"name": "Data TCM", "file": "dtcm.bin", "size": 0x4000},
            {"name": "Shared WRAM", "file": "shared_wram.bin", "size": 0x8000},
            {"name": "ARM7 WRAM", "file": "arm7_wram.bin", "size": 0x10000},
            {"name": "SRAM", "file": "sram.bin", "size": 0x80000},
        ]

        # RAM + screenshot + registers are captured by one bridge operation.
        bridge_res = await transport.request(
            "memory.dump_universal",
            {
                "dump_dir": str(target_folder.resolve()).replace("\\", "/"),
                "bin_path": str(bin_path.resolve()).replace("\\", "/"),
                "png_path": str(png_path.resolve()).replace("\\", "/"),
                "domain": "Main RAM",
                "size": MAIN_RAM_SIZE,
                "domains": domains_spec,
            },
        )
        physical_frame = int(bridge_res.get("frame") or semantic_frame or 0)
        raw_regs = bridge_res.get("registers", {}) or {}

        actual_ram_bytes = bin_path.stat().st_size if bin_path.exists() else 0
        bridge_written_bytes = int(bridge_res.get("written_bytes", 0) or 0)
        ram_ok = actual_ram_bytes == MAIN_RAM_SIZE and bridge_written_bytes == MAIN_RAM_SIZE
        screen_ok = (
            bool(bridge_res.get("screenshot_saved", False))
            and png_path.exists()
            and png_path.stat().st_size > 0
        )

        if not ram_ok:
            capture_errors.append(
                f"main_ram.bin incomplete: bridge={bridge_written_bytes}, file={actual_ram_bytes}, expected={MAIN_RAM_SIZE}"
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

        capture_complete = ram_ok and screen_ok
        files: Dict[str, str] = {}
        for key, path in (
            ("main_ram_bin", bin_path),
            ("screen_png", png_path),
            ("semantic_state_json", semantic_path),
            ("registers_json", registers_path),
            ("critical_ranges_json", critical_path),
            ("heap_candidates_json", heap_path),
            ("metadata_json", metadata_path),
        ):
            if path.exists():
                files[key] = path.name

        artifact_paths = [
            bin_path,
            png_path,
            semantic_path,
            registers_path,
            critical_path,
            heap_path,
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
            actors=[],
            map=m_ctx,
            dialogue=d_ctx,
            raw_state_dump=curr_state.model_dump(),
        )
        _write_json(manifest_path, asdict(manifest))

        pre_bundle_files = [
            "main_ram.bin",
            "itcm.bin",
            "dtcm.bin",
            "shared_wram.bin",
            "arm7_wram.bin",
            "sram.bin",
            "screen.png",
            "manifest.json",
            "metadata.json",
            "semantic_state.json",
            "registers.json",
            "critical_ranges.json",
            "heap_candidates.json",
        ]
        integrity_payload = {
            "schema": "snapshot_integrity/v1",
            "snapshot_id": folder_name,
            "physical_dump_frame": physical_frame,
            "expected_main_ram_bytes": MAIN_RAM_SIZE,
            "capture_complete": capture_complete,
            "capture_errors": capture_errors,
            "artifacts": {
                file_name: _artifact_meta(target_folder / file_name)
                for file_name in pre_bundle_files
            },
        }
        _write_json(integrity_path, integrity_payload)

        bundle_files = pre_bundle_files + ["integrity.json"]
        bundle_path = _build_bundle(target_folder, folder_name, bundle_files)
        bundle_meta = _artifact_meta(bundle_path)

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
            "capture_errors": capture_errors,
            "artifacts": integrity_payload["artifacts"],
            "bundle": {
                **bundle_meta,
                "file_name": bundle_path.name,
                "url": f"/dumps/{folder_name}/{bundle_path.name}",
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
                bundle_candidates = list(directory.glob("*.zip"))
                bundle_path = bundle_candidates[0] if bundle_candidates else None

                ram_size = bin_path.stat().st_size if bin_path.exists() else 0
                png_size = png_path.stat().st_size if png_path.exists() else 0
                actual_complete = ram_size == MAIN_RAM_SIZE and png_size > 0
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
                        "bin_url": f"/dumps/{directory.name}/main_ram.bin" if bin_path.exists() else None,
                        "png_url": f"/dumps/{directory.name}/screen.png" if png_path.exists() else None,
                        "manifest_url": f"/dumps/{directory.name}/manifest.json" if manifest_path.exists() else None,
                        "integrity_url": f"/dumps/{directory.name}/integrity.json" if integrity_path.exists() else None,
                        "bundle_url": f"/dumps/{directory.name}/{bundle_path.name}" if bundle_path else None,
                        "player": data.get("player", {}),
                        "map": data.get("map", {}),
                        "dialogue": dialogue,
                        "registers_count": len(registers),
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue

        return entries[:limit]


universal_snapshot_manager = UniversalSnapshotManager()
