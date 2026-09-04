#!/usr/bin/env python3
"""Reproducible RAM-only probes for Pokémon Black 2 runtime discovery.

This tool deliberately separates raw observations from interpretations.  It does
not claim that a loaded MsgBuffer string is visible until a TextPrinter/Window
field has been validated by a controlled experiment.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import requests


API_BASE = "http://127.0.0.1:8765"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT = PROJECT_ROOT / "reverse_engineering" / "experiments" / "EXP_009_text_printer_revalidation"

# Main-RAM offsets, never ARM9 absolute addresses.  These are observation
# ranges, not assertions that every field within them belongs to TextPrinter.
DEFAULT_RANGES = {
    "script_and_message_state": (0x247500, 0x200),
    "msg_buffer": (0x2490A0, 0x200),
    "printer_candidate": (0x31FCB0, 0x80),
    "printer_pointer_candidates": (0x332C00, 0x100),
    "player_actor_candidate": (0x23DE00, 0x100),
}

# This profile deliberately covers a complete candidate pixel allocation rather
# than a handful of alleged ``TextPrinter`` scalar fields.  It was chosen from
# the active dialogue allocation graph observed in EXP_012: the allocation
# tagged ``bmp.c`` begins at 0x02335394 and has a 0xF1C-byte payload whose
# candidate pixel data begins at 0x023353C0.  Its meaning is still a candidate;
# the bounded A-edge experiment is what tests it.
DIALOGUE_RENDER_RANGES = {
    "script_work_context": (0x247300, 0x600),
    "msg_buffer": (0x2490A0, 0x200),
    "dialogue_control_allocations": (0x332B40, 0x200),
    "dialogue_render_graph": (0x334B00, 0x300),
    "candidate_bitmap_surface": (0x335380, 0x1000),
    "printer_candidate": (0x31FCB0, 0x80),
}

# A focused pointer-target read for the next TextPrinter/Window experiment.
# It intentionally captures the bytes between the observed control pointer and
# the former surface window; no range name asserts that any target is the live
# screen bitmap.
TEXT_RENDER_CHAIN_RANGES = {
    "script_and_window_flags": (0x247500, 0x200),
    "dialogue_window_flag_candidate": (0x23B4E0, 0x40),
    "msg_buffer": (0x2490A0, 0x200),
    "tcbl_observation_and_pointer_targets": (0x332B40, 0x200),
    "candidate_render_context": (0x335300, 0x200),
    "candidate_render_allocation": (0x335380, 0x1000),
}

# The actor profile retains the render controls while widening around the live
# ScriptWork allocation and the only in-context actor-shaped pointer observed
# so far.  ``script_actor_candidate`` is not called a FieldActor: EXP_014 will
# test that claim by pointer relationships and a dialogue-end lifecycle.
DIALOGUE_ACTOR_RANGES = {
    "script_work_context": (0x247300, 0x600),
    "msg_buffer": (0x2490A0, 0x200),
    "dialogue_control_allocations": (0x332B40, 0x200),
    "candidate_bitmap_surface": (0x335380, 0x1000),
    "script_actor_candidate": (0x23DC00, 0x600),
    "former_player_candidate": (0x23DE00, 0x200),
}

# Focused, same-frame windows for validating the discovered ScriptWork →
# FieldActorSystem → ActorHeap relationship.  These are RAM observation
# windows only; their names describe the experiment, not verified types.
ACTOR_CHAIN_RANGES = {
    "script_work_context": (0x247300, 0x600),
    "game_system_candidate_via_scriptwork": (0x23B480, 0x100),
    "field_candidate_via_scriptwork": (0x263500, 0x100),
    "field_player_candidate": (0x324740, 0x100),
    "actor_system_candidate": (0x23DB40, 0x100),
    "actor_heap_candidate": (0x23DBE4, 0xC00),
    "actor_slot_table_candidate": (0x3086C0, 0x300),
    "parent_actor_context_candidate": (0x30AE80, 0x100),
    "field_candidate_via_actor_system_ref": (0x23B6D8, 0xA0),
    "actor_system_link_target_candidate": (0x241C40, 0x200),
}

RANGE_PROFILES = {
    "default": DEFAULT_RANGES,
    "dialogue-render": DIALOGUE_RENDER_RANGES,
    "text-render-chain": TEXT_RENDER_CHAIN_RANGES,
    "dialogue-actor": DIALOGUE_ACTOR_RANGES,
    "actor-chain": ACTOR_CHAIN_RANGES,
}


@dataclass(frozen=True)
class ObservationField:
    address: str
    offset: str
    type: str
    value: int
    confidence: str
    note: str


def _request(method: str, path: str, **kwargs: Any) -> Any:
    response = requests.request(method, f"{API_BASE}{path}", timeout=10, **kwargs)
    response.raise_for_status()
    return response.json()


def _atomic_dumps(ranges: dict[str, tuple[int, int]]) -> tuple[int | None, dict[str, dict[str, Any]]]:
    """Read named ranges at one Lua-bridge frame and retain that exact frame."""
    payload = {
        "ranges": [
            {"id": name, "domain": "Main RAM", "offset": offset, "length": length}
            for name, (offset, length) in ranges.items()
        ]
    }
    response = _request("POST", "/api/dev/memory_batch_snapshot", json=payload)
    blocks = response.get("results", {})
    if set(blocks) != set(ranges):
        raise RuntimeError(
            f"atomic batch returned ids {sorted(blocks)} for requested {sorted(ranges)}"
        )
    return response.get("frame"), {
        name: {
            "domain": "Main RAM",
            "offset": f"0x{int(block['offset']):06X}",
            "address": f"0x{0x02000000 + int(block['offset']):08X}",
            "length": int(block["length"]),
            "hex": block["hex"],
        }
        for name, block in blocks.items()
    }


def _u16(raw: bytes, at: int) -> int:
    return int.from_bytes(raw[at:at + 2], "little") if at + 2 <= len(raw) else 0


def _u32(raw: bytes, at: int) -> int:
    return int.from_bytes(raw[at:at + 4], "little") if at + 4 <= len(raw) else 0


def _decode_words(raw: bytes) -> list[int]:
    return [_u16(raw, index) for index in range(0, len(raw) - 1, 2)]


def _decode_loaded_text(raw: bytes) -> list[dict[str, Any]]:
    """List loadable UCS-2 fragments without assigning page/line semantics."""
    fragments: list[dict[str, Any]] = []
    current: list[str] = []
    start: int | None = None
    controls: list[str] = []

    def flush() -> None:
        nonlocal current, start, controls
        text = "".join(current).strip()
        if len(text) >= 2 and sum("\u4e00" <= char <= "\u9fff" for char in text) >= 2:
            fragments.append({"offset": start, "text": text, "control_words": controls})
        current, start, controls = [], None, []

    for word_index, word in enumerate(_decode_words(raw)):
        byte_offset = word_index * 2
        if word in (0xFFFF, 0xF000, 0xBE00, 0xBE01):
            controls.append(f"0x{word:04X}")
            flush()
        elif word == 0xFFFE:
            controls.append("0xFFFE")
        elif word == 0x000A:
            controls.append("0x000A")
            if current:
                current.append("\n")
        elif 0x4E00 <= word <= 0x9FFF or 0x3000 <= word <= 0x30FF:
            if start is None:
                start = byte_offset
            current.append(chr(word))
        elif 0x20 <= word <= 0x7E:
            if start is not None:
                current.append(chr(word))
        elif 0xFF01 <= word <= 0xFF5E:
            if start is None:
                start = byte_offset
            current.append(chr(word - 0xFEE0))
        elif word == 0 and current:
            flush()
        else:
            flush()
    flush()
    return fragments


def _printer_candidate_fields(raw: bytes) -> list[ObservationField]:
    # These offsets were carried over from a previous experiment.  The tool
    # records them to test them; confidence intentionally remains candidate.
    base = 0x0231FCB0
    definitions = ((0x04, "u16", "line_index_candidate"), (0x18, "u32", "source_pointer_candidate"),
                   (0x22, "u16", "scroll_distance_candidate"), (0x38, "u16", "cursor_x_candidate"),
                   (0x3A, "u16", "cursor_y_candidate"))
    output = []
    for rel, kind, note in definitions:
        value = _u32(raw, rel) if kind == "u32" else _u16(raw, rel)
        output.append(ObservationField(
            address=f"0x{base + rel:08X}", offset=f"+0x{rel:02X}", type=kind, value=value,
            confidence="candidate", note=note,
        ))
    return output


def snapshot(experiment: Path, label: str, ranges: dict[str, tuple[int, int]] = DEFAULT_RANGES) -> Path:
    experiment.mkdir(parents=True, exist_ok=True)
    batch_frame, dumps = _atomic_dumps(ranges)
    # State is requested afterwards for diagnostic context only.  It is never
    # treated as if it shares the raw batch's exact frame.
    state = _request("GET", "/api/state")
    printer_raw = bytes.fromhex(dumps.get("printer_candidate", {}).get("hex", ""))
    msg_raw = bytes.fromhex(dumps.get("msg_buffer", {}).get("hex", ""))
    result = {
        "label": label,
        "captured_at_unix": time.time(),
        "frame_from_batch": batch_frame,
        "frame_from_state": state.get("frame"),
        "method": "RAM-only exact-frame Main-RAM read_batch; semantic state is sampled separately and is not used as visible-text evidence.",
        "semantic_state_untrusted_for_visible_text": state,
        "raw_dumps": dumps,
        "decoded_loaded_msg_fragments": _decode_loaded_text(msg_raw),
        "printer_field_observations": (
            [asdict(item) for item in _printer_candidate_fields(printer_raw)]
            if printer_raw else []
        ),
        "assertions": [
            "Loaded MsgBuffer fragments are not labelled as current visible lines.",
            "All TextPrinter offsets in this snapshot are candidates unless validated by a stage transition.",
        ],
    }
    destination = experiment / f"snapshot_{label}.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def memory_diff(before: Path, after: Path, destination: Path) -> Path:
    left, right = load_snapshot(before), load_snapshot(after)
    changed: list[dict[str, Any]] = []
    for name in sorted(set(left["raw_dumps"]) & set(right["raw_dumps"])):
        lhs = bytes.fromhex(left["raw_dumps"][name].get("hex", ""))
        rhs = bytes.fromhex(right["raw_dumps"][name].get("hex", ""))
        base = int(left["raw_dumps"][name]["offset"], 0)
        for index, (a, b) in enumerate(zip(lhs, rhs)):
            if a != b:
                changed.append({"range": name, "offset": f"0x{base + index:06X}", "address": f"0x{0x02000000 + base + index:08X}", "before": a, "after": b})
    report = {"before": str(before), "after": str(after), "changed_byte_count": len(changed), "changes": changed}
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def pointer_search(blob: bytes, target: int) -> list[int]:
    needle = target.to_bytes(4, "little")
    return [match.start() for match in re.finditer(re.escape(needle), blob)]


def pointer_scan(
    experiment: Path,
    target: int,
    label: str,
    start: int = 0,
    size: int = 0x400000,
    limit: int = 256,
) -> Path:
    """Run one bounded bridge-owned reverse-pointer scan and save its frame."""
    if not 0 <= target <= 0xFFFFFFFF:
        raise ValueError("target must be a u32")
    experiment.mkdir(parents=True, exist_ok=True)
    payload = _request(
        "POST",
        "/api/dev/memory_pattern_scan",
        json={
            "bytes": list(target.to_bytes(4, "little")),
            "start": start,
            "size": size,
            "limit": limit,
            "domain": "Main RAM",
        },
    )
    evidence = {
        "schema": "pointer_scan/v1",
        "captured_at_unix": time.time(),
        "method": "bridge-owned memory.scan_pattern; read-only Main-RAM scan",
        "target_u32": f"0x{target:08X}",
        "target_little_endian_hex": target.to_bytes(4, "little").hex(),
        "request": {"start": start, "size": size, "limit": limit},
        "response": payload,
        "assertions": [
            "A raw pointer match establishes only a memory reference, not ownership or type.",
            "A pointer target must still pass structure-coherence and lifecycle tests.",
        ],
    }
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_.") or "pointer"
    destination = experiment / f"pointer_scan_{safe_label}.json"
    suffix = 2
    while destination.exists():
        destination = experiment / f"pointer_scan_{safe_label}_{suffix:02d}.json"
        suffix += 1
    destination.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def candidate_filter(items: Iterable[dict[str, Any]], required: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in items if all(item.get(key) == value for key, value in required.items())]


def candidate_rank(items: Iterable[dict[str, Any]], weights: dict[str, float]) -> list[dict[str, Any]]:
    ranked = []
    for item in items:
        score = sum(float(item.get(key, 0)) * weight for key, weight in weights.items())
        ranked.append({**item, "score": score})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def structure_scan(blob: bytes, base_offset: int, pattern_hex: str) -> list[dict[str, str]]:
    pattern = bytes.fromhex(pattern_hex)
    return [{"offset": f"0x{base_offset + index:06X}", "address": f"0x{0x02000000 + base_offset + index:08X}"}
            for index in [match.start() for match in re.finditer(re.escape(pattern), blob)]]


def input_sequence(
    experiment: Path,
    sequence: list[tuple[str, int]],
    ranges: dict[str, tuple[int, int]] = DEFAULT_RANGES,
) -> list[Path]:
    # A repeated invocation must preserve earlier evidence instead of silently
    # overwriting `snapshot_step_01_*`.
    run_ids = [
        int(match.group(1))
        for path in experiment.glob("snapshot_run*_before_sequence.json")
        if (match := re.search(r"snapshot_run(\d+)_", path.name))
    ]
    run_id = max(run_ids, default=0) + 1
    results: list[Path] = [snapshot(experiment, f"run{run_id:02d}_before_sequence", ranges)]
    for index, (button, frames) in enumerate(sequence, start=1):
        _request("POST", "/api/actions/press", json={"button": button, "frames": frames})
        time.sleep(0.5)
        results.append(snapshot(experiment, f"run{run_id:02d}_step_{index:02d}_{button.lower()}", ranges))
    return results


def a_edge_capture(
    experiment: Path,
    button: str,
    sample_frames: int,
    label: str,
    ranges: dict[str, tuple[int, int]] = DEFAULT_RANGES,
) -> Path:
    """Ask the Lua bridge to capture raw ranges around one input edge.

    Unlike :func:`input_sequence`, the emulator owns the timing here: it reads
    ``before_edge`` and every subsequent sample from its own frame loop.  The
    output stays raw; no sample is called a visible line or a speaker.
    """
    if not 1 <= sample_frames <= 120:
        raise ValueError("sample_frames must be in 1..120")
    experiment.mkdir(parents=True, exist_ok=True)
    range_profile = next((name for name, profile in RANGE_PROFILES.items() if profile == ranges), "custom")
    capture_ranges = [
        {"id": name, "domain": "Main RAM", "offset": offset, "length": length}
        for name, (offset, length) in ranges.items()
    ]
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_.") or "edge"
    started = _request("POST", "/api/dev/a_edge_capture", json={
        "button": button, "sample_frames": sample_frames, "ranges": capture_ranges,
    })
    deadline = time.monotonic() + max(10.0, sample_frames / 10.0 + 5.0)
    progress: dict[str, Any] = started
    while time.monotonic() < deadline:
        progress = _request("GET", "/api/dev/a_edge_capture")
        if progress.get("complete"):
            result = {
                "captured_at_unix": time.time(),
                "method": "bridge-owned button edge; before_edge and after_frame_N reads occur in the BizHawk Lua frame loop",
                "button": button,
                "requested_sample_frames": sample_frames,
                "range_profile": range_profile,
                "capture": progress,
                "assertions": [
                    "This artifact contains RAM observations only.",
                    "MsgBuffer strings and message pointers are not labelled as visible text.",
                    "No speaker actor is inferred without a verified ScriptWork-to-ActorSystem relation.",
                ],
            }
            destination = experiment / f"a_edge_capture_{safe_label}.json"
            suffix = 2
            while destination.exists():
                destination = experiment / f"a_edge_capture_{safe_label}_{suffix:02d}.json"
                suffix += 1
            destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return destination
        time.sleep(0.05)
    raise TimeoutError(f"A-edge capture did not finish: {progress}")


def memory_write_trace(
    experiment: Path,
    start_addr: int,
    length: int,
    addresses: list[int],
    max_frames: int,
    max_events: int,
    button: str | None,
    label: str,
    ranges: dict[str, tuple[int, int]],
) -> Path:
    """Save a finite, address-specific ARM9 writer-PC trace as raw evidence.

    The bridge installs its callbacks before the optional one-frame button
    edge.  It is intentionally limited to a few *known changed addresses*,
    rather than a global write callback over the complete Bitmap allocation.
    Function names and visible text remain unresolved until a later static
    call-path review.
    """
    if not 0x02000000 <= start_addr < 0x02400000:
        raise ValueError("start_addr must be an ARM9 Main-RAM address")
    if not 1 <= length <= 0x4000 or start_addr + length > 0x02400000:
        raise ValueError("length must stay within Main RAM and be <= 0x4000")
    if not 1 <= len(addresses) <= 16 or len(set(addresses)) != len(addresses):
        raise ValueError("addresses must contain 1..16 distinct watches")
    if any(not start_addr <= address < start_addr + length for address in addresses):
        raise ValueError("each watch address must be inside the trace range")
    if not 1 <= max_frames <= 3 or not 1 <= max_events <= 64:
        raise ValueError("address-specific trace limits are frames=1..3 and events=1..64")

    experiment.mkdir(parents=True, exist_ok=True)
    capture_ranges = [
        {"id": name, "domain": "Main RAM", "offset": offset, "length": size}
        for name, (offset, size) in ranges.items()
    ]
    started = _request("POST", "/api/dev/memory_write_trace", json={
        "start_addr": start_addr,
        "length": length,
        "addresses": addresses,
        "max_frames": max_frames,
        "max_events": max_events,
        "button": button,
        "ranges": capture_ranges,
    })
    deadline = time.monotonic() + 20.0
    progress: dict[str, Any] = started
    while time.monotonic() < deadline:
        time.sleep(0.10)
        progress = _request("GET", "/api/dev/memory_write_trace")
        if progress.get("complete"):
            evidence = {
                "schema": "bounded_address_write_pc_trace/v1",
                "captured_at_unix": time.time(),
                "method": "BizHawk address-specific event.on_bus_write callbacks armed before optional one-frame input",
                "request": {
                    "start_addr": f"0x{start_addr:08X}",
                    "length": length,
                    "addresses": [f"0x{address:08X}" for address in addresses],
                    "max_frames": max_frames,
                    "max_events": max_events,
                    "button": button,
                    "watch_ranges": capture_ranges,
                },
                "bridge_response": progress,
                "guardrails": [
                    "The trace is read-only and automatically unregisters every address callback at the frame or event limit.",
                    "A writer PC establishes only a candidate RAM-writing instruction; its function/type must be checked against current-ROM code.",
                    "No visible glyph, screen line, TextPrinter state enum, or speaker is inferred by this capture tool.",
                ],
            }
            safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_.") or "write_trace"
            destination = experiment / f"memory_write_trace_{safe_label}.json"
            suffix = 2
            while destination.exists():
                destination = experiment / f"memory_write_trace_{safe_label}_{suffix:02d}.json"
                suffix += 1
            destination.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            return destination
    try:
        _request("DELETE", "/api/dev/memory_write_trace")
    except requests.RequestException:
        pass
    raise TimeoutError(f"write-PC trace did not finish: {progress}")


def report_generation(experiment: Path) -> Path:
    snapshots = sorted(experiment.glob("snapshot_*.json"))
    lines = [
        "# TEST REPORT", "", "## Goal", "", "Revalidate candidate TextPrinter fields from RAM without treating preloaded message text as visible output.",
        "", "## Hypothesis", "", "`0x0231FCB0` is an active TextPrinter whose cursor and source pointer describe the currently visible dialogue.",
        "", "## Method", "", "Read only Main RAM through the BizHawk bridge with `memory.read_batch`; every raw range within a snapshot is one frame. Input and subsequent snapshots remain separate observations.",
        "", "## Actions performed", "",
    ]
    for item in snapshots:
        data = load_snapshot(item)
        lines.append(f"- `{item.name}` — frame `{data.get('frame_from_state')}`, label `{data.get('label')}`")
    lines += [
        "", "## Memory ranges", "", "- `0x02247500–0x022476FF`: Script/message state", "- `0x022490A0–0x0224929F`: loaded MsgBuffer", "- `0x0231FCB0–0x0231FD2F`: rejected printer candidate", "- `0x02332C00–0x02332CFF`: pointer candidates", "- `0x0223DE00–0x0223DEFF`: player actor candidate", 
        "", "## Candidate addresses", "", "- `0x0231FCC8 = 0x00327073`, which is not a pointer into Main RAM.", "- `0x0231FCEA = 0x3000`, inconsistent with a dialogue cursor Y position.", "- `0x02332C4C = 0x022490EC`, a pointer to a preloaded final text fragment; it is not evidence that the fragment is visible.",
        "", "## Raw observations", "", "- The loaded buffer includes `科学的力量真是惊人!`, `现在可以用通信和100个人`, and `同时游戏!` as separate fragments.", "- The middle fragment contains `0xFFFE` controls. Its line break cannot be inferred by mapping every control word to newline.",
        "", "## SWAN correspondence", "", "- `field_mmodel.h` remains relevant to actor discovery, but SWAN supplied no verified TextPrinter layout for this ROM build.",
        "", "## Supporting and opposing evidence", "", "- Support: historical EXP_008 values varied across samples.", "- Opposing: the current raw bytes at the claimed pointer/cursor offsets are structurally incompatible with those interpretations.",
        "", "## Confidence", "", "- `0x0231FCB0` as active TextPrinter: rejected for this base.", "- MsgBuffer fragment extraction: verified loaded data.", "- Current visible lines, print state, and speaker actor: unresolved.",
        "", "## Verified fields", "", "- None added by this report. A candidate requires controlled stage-transition correlation before promotion.",
        "", "## Unresolved fields", "", "- Active TextPrinter object, Window binding, visible top-line index, current glyph pointer, script context / speaking actor.",
        "", "## Files changed", "", "- `tools/runtime_memory_discovery.py`", "- `docs/swan_runtime_schema.md`", "- dialogue decoder/state schema (visible-text claims are now unresolved until proven)",
        "", "## Next recommended experiment", "", "Capture a frame-bounded A-edge sequence (before edge, after one frame, each scroll frame, settled wait) with raw Message, candidate printer, Window, ScriptWork, and ActorSystem ranges in one bridge batch. Then stop for review.",
    ]
    destination = experiment / "report.md"
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument(
        "--range-profile",
        choices=sorted(RANGE_PROFILES),
        default="default",
        help="Raw memory profile used by snapshot and A-edge commands.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_snapshot = sub.add_parser("snapshot")
    p_snapshot.add_argument("label")
    p_sequence = sub.add_parser("input-sequence")
    p_sequence.add_argument("steps", nargs="+", help="BUTTON:frames, e.g. A:1 A:1")
    p_diff = sub.add_parser("memory-diff")
    p_diff.add_argument("before", type=Path)
    p_diff.add_argument("after", type=Path)
    p_diff.add_argument("--output", type=Path)
    p_edge = sub.add_parser("a-edge-capture", help="Capture before/A-edge/per-frame RAM via the reloaded Lua bridge")
    p_edge.add_argument("--button", default="A")
    p_edge.add_argument("--sample-frames", type=int, default=32)
    p_edge.add_argument("--label", default="edge", help="Evidence label, e.g. page1_to_page2")
    p_trace = sub.add_parser("write-pc-trace", help="Capture a bounded address-specific ARM9 write-PC trace")
    p_trace.add_argument("--start-addr", type=lambda value: int(value, 0), default=0x023353C0)
    p_trace.add_argument("--length", type=lambda value: int(value, 0), default=0xF00)
    p_trace.add_argument("--addresses", nargs="+", required=True, type=lambda value: int(value, 0), help="1..16 known changed ARM9 byte/aligned addresses")
    p_trace.add_argument("--max-frames", type=int, default=3)
    p_trace.add_argument("--max-events", type=int, default=32)
    p_trace.add_argument("--button", default="A", help="One-frame button edge after callback arm (default A)")
    p_trace.add_argument("--passive", action="store_true", help="Observe only; do not inject a button")
    p_trace.add_argument("--label", default="write_trace")
    p_ptr = sub.add_parser("pointer-scan", help="Save a bounded reverse-pointer scan with its bridge frame")
    p_ptr.add_argument("target", help="u32 target, e.g. 0x0223DCE4")
    p_ptr.add_argument("--label", default="pointer")
    p_ptr.add_argument("--start", type=lambda value: int(value, 0), default=0)
    p_ptr.add_argument("--size", type=lambda value: int(value, 0), default=0x400000)
    p_ptr.add_argument("--limit", type=int, default=256)
    sub.add_parser("report-generation")
    args = parser.parse_args()

    if args.command == "snapshot":
        output = snapshot(args.experiment, args.label, RANGE_PROFILES[args.range_profile])
    elif args.command == "input-sequence":
        sequence = []
        for step in args.steps:
            button, sep, frames = step.partition(":")
            if not sep or not button or not frames.isdigit():
                parser.error(f"Invalid step {step!r}; use BUTTON:frames")
            sequence.append((button, int(frames)))
        output = input_sequence(args.experiment, sequence, RANGE_PROFILES[args.range_profile])
    elif args.command == "memory-diff":
        output = memory_diff(args.before, args.after, args.output or args.experiment / "memory_diff.json")
    elif args.command == "a-edge-capture":
        output = a_edge_capture(
            args.experiment,
            args.button,
            args.sample_frames,
            args.label,
            RANGE_PROFILES[args.range_profile],
        )
    elif args.command == "write-pc-trace":
        output = memory_write_trace(
            args.experiment,
            args.start_addr,
            args.length,
            args.addresses,
            args.max_frames,
            args.max_events,
            None if args.passive else args.button,
            args.label,
            RANGE_PROFILES[args.range_profile],
        )
    elif args.command == "pointer-scan":
        if not 1 <= args.limit <= 256:
            parser.error("pointer-scan --limit must be in 1..256")
        output = pointer_scan(
            args.experiment,
            int(args.target, 0),
            args.label,
            args.start,
            args.size,
            args.limit,
        )
    else:
        output = report_generation(args.experiment)
    print(json.dumps([str(item) for item in output] if isinstance(output, list) else str(output), ensure_ascii=False))


if __name__ == "__main__":
    main()
