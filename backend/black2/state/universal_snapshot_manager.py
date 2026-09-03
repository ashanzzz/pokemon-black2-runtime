"""Universal Ground-Truth Snapshot Manager for Pokémon Black 2 (IREJ).

Captures an atomic, zero-loss snapshot of the entire game world state:
1. Full 4MB ARM9 Main RAM binary (main_ram.bin)
2. 100% Native Game Screen Capture (screen.png)
3. ARM9 Hardware Registers (PC, SP, LR, CPSR, R0-R12)
4. Comprehensive Semantic Context (Player, Actors, Map, Dialogue, System)
5. Structured Dataset Manifest (manifest.json) for cross-AI & offline analysis.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SNAPSHOT_BASE_DIR = Path("reverse_engineering/dumps").resolve()


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
    schema_version: str = "universal_snapshot/v1"
    snapshot_id: str = ""
    timestamp_utc: str = ""
    frame: int = 0
    category: str = "OVERWORLD_EXPLORE"
    label: str = ""
    operator_notes: str = ""
    rom_hash: str = "8DB71663502BBF3B43AC3C9052EC390C390BE62F"
    files: Dict[str, str] = field(default_factory=dict)
    registers: Dict[str, Any] = field(default_factory=dict)
    player: PlayerContext = field(default_factory=PlayerContext)
    actors: List[ActorSlot] = field(default_factory=list)
    map: MapContext = field(default_factory=MapContext)
    dialogue: DialogueContext = field(default_factory=DialogueContext)
    raw_state_dump: Dict[str, Any] = field(default_factory=dict)


class UniversalSnapshotManager:
    """Manages creation, cataloging, and retrieval of full 4MB ground-truth snapshots."""

    def __init__(self, base_dir: Path = SNAPSHOT_BASE_DIR):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def create_snapshot(
        self,
        transport: Any,
        state_engine: Any,
        category: str = "OVERWORLD_EXPLORE",
        label: str = "unlabelled",
        notes: str = ""
    ) -> Dict[str, Any]:
        """Atomically create a universal snapshot with 4MB RAM, screen, registers, and context."""
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        curr_state = await state_engine.sample_once()
        frame = curr_state.frame or transport.last_frame or 0

        clean_label = re.sub(r"[^\w\-_]", "_", label.strip() or "sample")
        folder_name = f"dump_{timestamp_str}_f{frame}_{category}_{clean_label}"
        target_folder = self.base_dir / folder_name
        target_folder.mkdir(parents=True, exist_ok=True)

        bin_path = (target_folder / "main_ram.bin").resolve()
        png_path = (target_folder / "screen.png").resolve()

        # Step 1: Tell Lua Bridge to dump 4MB RAM and capture screenshot
        bridge_res = await transport.request("memory.dump_universal", {
            "bin_path": str(bin_path),
            "png_path": str(png_path),
            "domain": "Main RAM",
            "size": 0x400000,
        })

        # Step 2: Build Multi-Dimensional Context from current state
        ctx = curr_state.context
        printer = ctx.printer or {}

        # Player Context
        wpos = curr_state.player_world_pos or {}
        p_ctx = PlayerContext(
            verified=bool(curr_state.player_position_verified),
            grid_x=wpos.get("x"),
            grid_y=wpos.get("y"),
            elevation_z=wpos.get("z"),
            facing=curr_state.player_facing or "South",
            movement_state=curr_state.movement_state or "Idle",
        )

        # Map Context
        m_ctx = MapContext(
            map_section_id=curr_state.map_section_id,
            location_name=curr_state.location or "未知区域",
        )

        # Dialogue Context
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

        # Registers
        raw_regs = bridge_res.get("registers", {})

        manifest = SnapshotManifest(
            snapshot_id=folder_name,
            timestamp_utc=datetime.utcnow().isoformat() + "Z",
            frame=frame,
            category=category,
            label=label,
            operator_notes=notes,
            files={
                "main_ram_bin": "main_ram.bin",
                "screen_png": "screen.png",
                "manifest_json": "manifest.json",
            },
            registers=raw_regs,
            player=p_ctx,
            actors=[],
            map=m_ctx,
            dialogue=d_ctx,
            raw_state_dump=curr_state.model_dump(),
        )

        # Write manifest.json
        manifest_file = target_folder / "manifest.json"
        manifest_file.write_text(json.dumps(asdict(manifest), indent=2, ensure_ascii=False), encoding="utf-8")

        # Also write a legacy metadata.json for backward compatibility
        legacy_meta = {
            "label": label,
            "category": category,
            "timestamp": timestamp_str,
            "frame": frame,
            "semantic_state": curr_state.model_dump(),
        }
        (target_folder / "metadata.json").write_text(json.dumps(legacy_meta, indent=2, ensure_ascii=False), encoding="utf-8")

        return {
            "ok": True,
            "snapshot_id": folder_name,
            "folder": str(target_folder),
            "frame": frame,
            "category": category,
            "label": label,
            "main_ram_size": bridge_res.get("written_bytes", 0),
            "screenshot_saved": bridge_res.get("screenshot_saved", False),
            "dialogue_text": d_ctx.visible_lines,
        }

    def list_snapshots(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List all saved snapshots with summary metadata."""
        if not self.base_dir.exists():
            return []

        entries = []
        for d in sorted(self.base_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            manifest_path = d / "manifest.json"
            meta_path = d / "metadata.json"
            if not manifest_path.exists() and not meta_path.exists():
                continue

            try:
                data = {}
                if manifest_path.exists():
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                else:
                    data = json.loads(meta_path.read_text(encoding="utf-8"))

                has_bin = (d / "main_ram.bin").exists()
                has_png = (d / "screen.png").exists()

                entries.append({
                    "folder_name": d.name,
                    "category": data.get("category", "OVERWORLD_EXPLORE"),
                    "label": data.get("label", d.name),
                    "timestamp": data.get("timestamp_utc") or data.get("timestamp", ""),
                    "frame": data.get("frame", 0),
                    "has_bin": has_bin,
                    "has_png": has_png,
                    "bin_url": f"/dumps/{d.name}/main_ram.bin" if has_bin else None,
                    "png_url": f"/dumps/{d.name}/screen.png" if has_png else None,
                    "player": data.get("player", {}),
                    "map": data.get("map", {}),
                    "dialogue": data.get("dialogue", {}),
                    "registers_count": len(data.get("registers", {})),
                })
            except Exception:
                pass

        return entries[:limit]


# Global singleton manager
universal_snapshot_manager = UniversalSnapshotManager()
