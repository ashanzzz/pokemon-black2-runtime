#!/usr/bin/env python3
"""Re-index existing Universal Evidence snapshots without recapturing RAM.

Original dump folders are never rewritten.  Derived v5 sidecars are written to:
  reverse_engineering/derived/v5/snapshots/<snapshot_id>.json
  reverse_engineering/derived/v5/transition_evidence_v1.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.black2.world.runtime_field_resolver import resolve_runtime_field_from_ram  # noqa: E402

try:
    from backend.black2.world.map_truth_v3 import MapTruthV3  # noqa: E402
except Exception:
    MapTruthV3 = None  # type: ignore


MAIN_RAM_SIZE = 0x400000


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def dump_meta(folder: Path) -> dict[str, Any]:
    manifest = load_json(folder / "manifest.json")
    integrity = load_json(folder / "integrity.json")
    metadata = load_json(folder / "metadata.json")
    frame = (
        integrity.get("physical_dump_frame")
        or manifest.get("frame")
        or metadata.get("frame")
        or 0
    )
    return {
        "snapshot_id": manifest.get("snapshot_id") or folder.name,
        "label": manifest.get("label") or metadata.get("label") or folder.name,
        "category": manifest.get("category") or metadata.get("category"),
        "frame": int(frame or 0),
        "capture_complete": integrity.get("capture_complete", manifest.get("capture_complete")),
    }


def compact_state(item: dict[str, Any]) -> dict[str, Any]:
    runtime = item.get("runtime_field") or {}
    truth = item.get("map_truth") or {}
    mapper = runtime.get("mapper") or {}
    player = runtime.get("player") or {}
    props = runtime.get("props") or {}
    matrix_match = truth.get("matrix_match") or {}
    zone_identity = truth.get("zone_identity") or {}
    return {
        "snapshot_id": item.get("snapshot_id"),
        "label": item.get("label"),
        "frame": item.get("frame"),
        "runtime_status": runtime.get("status"),
        "runtime_confidence": runtime.get("confidence"),
        "zone_id": zone_identity.get("value", player.get("zone_id")),
        "matrix_id": matrix_match.get("selected_matrix_id"),
        "player_chunk": mapper.get("player_chunk"),
        "player_grid": ((player.get("actor") or {}).get("grid_position")),
        "door_count": len(props.get("doors") or []),
        "prop_instance_count": len(props.get("instances") or []),
    }


def transitions(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for before, after in zip(states, states[1:]):
        changed = []
        for key in ("zone_id", "matrix_id", "player_chunk"):
            if before.get(key) != after.get(key):
                changed.append(key)
        if not changed:
            continue
        result.append({
            "from": before,
            "to": after,
            "changed": changed,
            "frame_delta": int(after.get("frame") or 0) - int(before.get("frame") or 0),
            "classification": (
                "zone_transition" if "zone_id" in changed
                else "matrix_transition" if "matrix_id" in changed
                else "chunk_transition"
            ),
            "door_warp_semantics": "candidate until DoorUID/warp relation repeats across independent transitions",
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dumps", type=Path, default=PROJECT_ROOT / "reverse_engineering" / "dumps")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "reverse_engineering" / "derived" / "v5")
    parser.add_argument("--rom", type=str, default=os.getenv("BLACK2_ROM_PATH"))
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    sidecars = args.out / "snapshots"
    sidecars.mkdir(parents=True, exist_ok=True)

    truth_service = None
    if args.rom and MapTruthV3 is not None:
        try:
            truth_service = MapTruthV3(args.rom)
        except Exception as error:
            print(f"[v5] ROM join disabled: {error}")

    indexed: list[dict[str, Any]] = []
    folders = sorted(path for path in args.dumps.iterdir() if path.is_dir()) if args.dumps.is_dir() else []
    for folder in folders:
        ram_path = folder / "main_ram.bin"
        if not ram_path.is_file():
            continue
        meta = dump_meta(folder)
        ram = ram_path.read_bytes()
        record: dict[str, Any] = {**meta, "main_ram_size": len(ram)}
        if len(ram) != MAIN_RAM_SIZE:
            record["error"] = f"main_ram.bin has {len(ram)} bytes; expected {MAIN_RAM_SIZE}"
        else:
            runtime = resolve_runtime_field_from_ram(ram, frame=meta["frame"])
            record["runtime_field"] = runtime
            if truth_service is not None:
                try:
                    record["map_truth"] = truth_service.from_runtime(runtime, include_world=False)
                except Exception as error:
                    record["map_truth"] = {"status": "error", "reason": str(error)}
        out_path = sidecars / f"{meta['snapshot_id']}.json"
        out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        indexed.append(record)
        print(f"[v5] {meta['snapshot_id']}: {record.get('runtime_field', {}).get('status', record.get('error', 'unknown'))}")

    indexed.sort(key=lambda item: (int(item.get("frame") or 0), str(item.get("snapshot_id"))))
    states = [compact_state(item) for item in indexed if item.get("runtime_field")]
    report = {
        "format": "black2-transition-evidence/v1",
        "snapshot_count": len(indexed),
        "resolved_state_count": len(states),
        "rom_join_enabled": truth_service is not None,
        "source_policy": "derived sidecars only; original Universal Evidence files are immutable",
        "states": states,
        "transitions": transitions(states),
    }
    report_path = args.out / "transition_evidence_v1.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[v5] wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
