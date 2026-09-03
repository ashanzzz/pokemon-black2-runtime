#!/usr/bin/env python3
"""Summarise a bridge-owned dialogue-render capture without decoding pixels.

The result records byte-level evidence for a possible text-control block and
its candidate bitmap surface.  It intentionally does *not* use screenshots,
OCR, or an assumed tile layout, and it does not name any glyph as visible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


TCBL_WORD_ADDRESSES = tuple(range(0x02332C20, 0x02332C54, 4))
SCRIPT_MESSAGE_ACTIVE = 0x02247546


def _ranges(sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in sample["ranges"]}


def _raw(entry: dict[str, Any]) -> bytes:
    return bytes.fromhex(entry["hex"])


def _read_u32(entries: dict[str, dict[str, Any]], address: int) -> int | None:
    for entry in entries.values():
        base = int(entry["offset"])
        raw = _raw(entry)
        relative = address - (0x02000000 + base)
        if 0 <= relative <= len(raw) - 4:
            return int.from_bytes(raw[relative:relative + 4], "little")
    return None


def _read_u8(entries: dict[str, dict[str, Any]], address: int) -> int | None:
    for entry in entries.values():
        base = int(entry["offset"])
        raw = _raw(entry)
        relative = address - (0x02000000 + base)
        if 0 <= relative < len(raw):
            return raw[relative]
    return None


def _spans(left: bytes, right: bytes, base: int, max_spans: int = 30) -> dict[str, Any]:
    """Return changed byte count and contiguous address spans, truncating safely."""
    changed = [index for index, (a, b) in enumerate(zip(left, right)) if a != b]
    if len(left) != len(right):
        raise ValueError("capture range size changed during one experiment")
    spans: list[dict[str, str | int]] = []
    for index in changed:
        if not spans or index != spans[-1]["end_offset"] + 1:
            spans.append({"start_offset": index, "end_offset": index})
        else:
            spans[-1]["end_offset"] = index
    visible = [
        {
            "start_address": f"0x{0x02000000 + base + item['start_offset']:08X}",
            "end_address": f"0x{0x02000000 + base + item['end_offset']:08X}",
            "length": item["end_offset"] - item["start_offset"] + 1,
        }
        for item in spans[:max_spans]
    ]
    return {
        "changed_byte_count": len(changed),
        "changed_span_count": len(spans),
        "spans_truncated": len(spans) > len(visible),
        "spans": visible,
    }


def analyse(source: Path, destination: Path) -> Path:
    capture_doc = json.loads(source.read_text(encoding="utf-8"))
    samples = capture_doc["capture"]["samples"]
    if not samples or samples[0]["phase"] != "before_edge":
        raise ValueError("expected an A-edge capture beginning with before_edge")

    baseline = _ranges(samples[0])
    rows: list[dict[str, Any]] = []
    previous = baseline
    for ordinal, sample in enumerate(samples):
        current = _ranges(sample)
        surface = _raw(current["candidate_bitmap_surface"])
        baseline_surface = _raw(baseline["candidate_bitmap_surface"])
        prior_surface = _raw(previous["candidate_bitmap_surface"])
        tcb_values = {
            f"0x{address:08X}": (
                f"0x{value:08X}" if (value := _read_u32(current, address)) is not None else None
            )
            for address in TCBL_WORD_ADDRESSES
        }
        range_changes = {}
        for name in current:
            if name in baseline:
                range_changes[name] = {
                    "from_before_edge": _spans(
                        _raw(baseline[name]), _raw(current[name]), int(current[name]["offset"])
                    ),
                    "from_previous_sample": _spans(
                        _raw(previous[name]), _raw(current[name]), int(current[name]["offset"])
                    ),
                }
        rows.append({
            "ordinal": ordinal,
            "phase": sample["phase"],
            "bridge_frame": sample["frame"],
            "script_msg_active": _read_u8(current, SCRIPT_MESSAGE_ACTIVE),
            "tcbl_words": tcb_values,
            "candidate_surface_sha256": hashlib.sha256(surface).hexdigest(),
            "candidate_surface": {
                "from_before_edge": _spans(
                    baseline_surface, surface, int(current["candidate_bitmap_surface"]["offset"])
                ),
                "from_previous_sample": _spans(
                    prior_surface, surface, int(current["candidate_bitmap_surface"]["offset"])
                ),
            },
            "range_changes": range_changes,
        })
        previous = current

    result = {
        "schema": "dialogue_render_capture_analysis/v1",
        "source": str(source),
        "method": "byte-level range diffs and scalar reads from a bridge-owned per-frame capture",
        "guardrails": [
            "A changed candidate bitmap surface is not yet a verified visible window.",
            "No codepoint, line, scroll distance, printer state, or NPC actor is inferred by this tool.",
            "All addresses are ARM9 Main-RAM addresses reconstructed from recorded offsets.",
        ],
        "tcbl_word_addresses": [f"0x{address:08X}" for address in TCBL_WORD_ADDRESSES],
        "samples": rows,
    }
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.capture.with_name(f"{args.capture.stem}_analysis.json")
    print(analyse(args.capture, output))


if __name__ == "__main__":
    main()
