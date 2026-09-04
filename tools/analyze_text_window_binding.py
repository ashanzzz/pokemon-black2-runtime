#!/usr/bin/env python3
"""Analyze a candidate tcbl.c -> GFLBitmap -> pixel-memory chain.

This is a byte-level evidence tool, not OCR and not a screen renderer.  A
coherent pointer chain plus changing bytes establishes a candidate draw target;
it does not establish that the target belongs to the currently visible Window.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ARM9_BASE = 0x02000000
TCBL_BASE = 0x02332C20


def _raw(item: dict[str, Any]) -> bytes:
    return bytes(item.get("bytes", [])) if item.get("bytes") is not None else bytes.fromhex(item.get("hex", ""))


def _base(item: dict[str, Any]) -> int:
    value = int(item.get("offset", 0))
    return value if value >= ARM9_BASE else ARM9_BASE + value


def _ranges(sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in sample.get("ranges", []) if item.get("id")}


def _at(ranges: dict[str, dict[str, Any]], address: int, length: int) -> bytes | None:
    for item in ranges.values():
        raw = _raw(item)
        start = address - _base(item)
        if 0 <= start and start + length <= len(raw):
            return raw[start:start + length]
    return None


def _u8(ranges: dict[str, dict[str, Any]], address: int) -> int | None:
    data = _at(ranges, address, 1)
    return data[0] if data else None


def _u16(ranges: dict[str, dict[str, Any]], address: int) -> int | None:
    data = _at(ranges, address, 2)
    return int.from_bytes(data, "little") if data else None


def _u32(ranges: dict[str, dict[str, Any]], address: int) -> int | None:
    data = _at(ranges, address, 4)
    return int.from_bytes(data, "little") if data else None


def _hex(value: int | None) -> str | None:
    return f"0x{value:08X}" if value is not None else None


def _diff(left: bytes, right: bytes, base: int) -> dict[str, Any]:
    if len(left) != len(right):
        return {"error": "surface lengths differ"}
    changed = [index for index, (a, b) in enumerate(zip(left, right)) if a != b]
    spans: list[tuple[int, int]] = []
    for index in changed:
        if not spans or index != spans[-1][1] + 1:
            spans.append((index, index))
        else:
            spans[-1] = (spans[-1][0], index)
    return {
        "changed_byte_count": len(changed),
        "span_count": len(spans),
        "spans": [
            {
                "start": f"0x{base + start:08X}",
                "end": f"0x{base + end:08X}",
                "length": end - start + 1,
            }
            for start, end in spans[:32]
        ],
        "spans_truncated": len(spans) > 32,
    }


def _observation(ranges: dict[str, dict[str, Any]]) -> dict[str, Any]:
    bmpwin = _u32(ranges, TCBL_BASE + 0x20)
    bitmap = _u32(ranges, TCBL_BASE + 0x24)
    pixel = _u32(ranges, bitmap) if bitmap is not None else None
    width = _u16(ranges, (bitmap or 0) + 4) if bitmap is not None else None
    height = _u16(ranges, (bitmap or 0) + 6) if bitmap is not None else None
    pixel_length = width * height // 2 if width is not None and height is not None else None
    return {
        "tcbl_observation_base": _hex(TCBL_BASE),
        "phase_candidate_plus_18": _hex(_u32(ranges, TCBL_BASE + 0x18)),
        "first_page_latch_candidate_plus_1c": _hex(_u32(ranges, TCBL_BASE + 0x1C)),
        "bmpwin_pointer_hypothesis_plus_20": _hex(bmpwin),
        "bitmap_pointer_hypothesis_plus_24": _hex(bitmap),
        "context_pointer_hypothesis_plus_28": _hex(_u32(ranges, TCBL_BASE + 0x28)),
        "continuation_cursor_candidate_plus_2c": _hex(_u32(ranges, TCBL_BASE + 0x2C)),
        "scroll_progress_u8_candidate_plus_37": _u8(ranges, TCBL_BASE + 0x37),
        "cursor_x_u16_candidate_plus_4c": _u16(ranges, TCBL_BASE + 0x4C),
        "cursor_y_u16_candidate_plus_4e": _u16(ranges, TCBL_BASE + 0x4E),
        "line_step_u16_candidate_plus_52": _u16(ranges, TCBL_BASE + 0x52),
        "bmpwin_layout_candidate": {
            "object": _hex(bmpwin),
            "member_plus_0c_matches_bitmap_candidate": _hex(_u32(ranges, (bmpwin or 0) + 0x0C)) if bmpwin else None,
        },
        "gfl_bitmap_layout_candidate": {
            "object": _hex(bitmap),
            "pixel_data": _hex(pixel),
            "width_u16": width,
            "height_u16": height,
            "four_bpp_byte_length_hypothesis": pixel_length,
        },
        "script_msg_active": _u8(ranges, 0x02247546),
        "window_active_candidate": _u8(ranges, 0x0223B4F5),
    }


def analyze(capture: dict[str, Any]) -> dict[str, Any]:
    samples = capture.get("capture", {}).get("samples", [])
    if not samples or samples[0].get("phase") != "before_edge":
        raise ValueError("expected bridge-owned capture with before_edge")

    baseline_ranges = _ranges(samples[0])
    baseline_obs = _observation(baseline_ranges)
    baseline_pixel_hex = baseline_obs["gfl_bitmap_layout_candidate"]["pixel_data"]
    baseline_pixel = int(baseline_pixel_hex, 16) if baseline_pixel_hex else None
    baseline_length = baseline_obs["gfl_bitmap_layout_candidate"]["four_bpp_byte_length_hypothesis"]
    baseline_bytes = _at(baseline_ranges, baseline_pixel or 0, baseline_length or 0) if baseline_pixel and baseline_length else None

    output_samples: list[dict[str, Any]] = []
    previous_bytes = baseline_bytes
    for ordinal, sample in enumerate(samples):
        ranges = _ranges(sample)
        obs = _observation(ranges)
        pixel_hex = obs["gfl_bitmap_layout_candidate"]["pixel_data"]
        pixel = int(pixel_hex, 16) if pixel_hex else None
        length = obs["gfl_bitmap_layout_candidate"]["four_bpp_byte_length_hypothesis"]
        current_bytes = _at(ranges, pixel or 0, length or 0) if pixel and length else None
        entry: dict[str, Any] = {
            "ordinal": ordinal,
            "phase": sample.get("phase"),
            "bridge_frame": sample.get("frame"),
            "observation": obs,
            "pixel_target_sha256": hashlib.sha256(current_bytes).hexdigest() if current_bytes is not None else None,
            "pixel_target_in_captured_ranges": current_bytes is not None,
        }
        if current_bytes is not None and baseline_bytes is not None and pixel == baseline_pixel and length == baseline_length:
            entry["pixel_bytes_from_before_edge"] = _diff(baseline_bytes, current_bytes, pixel)
        if current_bytes is not None and previous_bytes is not None and len(current_bytes) == len(previous_bytes):
            entry["pixel_bytes_from_previous"] = _diff(previous_bytes, current_bytes, pixel or 0)
        output_samples.append(entry)
        previous_bytes = current_bytes

    return {
        "schema": "text_window_binding_analysis/v1",
        "method": "bridge-frame RAM pointer-target and byte-diff analysis; no screenshot/OCR",
        "guardrails": [
            "The tcbl.c offsets and pointer targets remain candidates until a draw/flush call path is traced.",
            "A GFLBitmap-shaped object and changing pixel bytes do not by themselves prove the active visible Window.",
            "No source codepoint, visual line, TextPrinter state enum, or speaker is inferred here.",
        ],
        "baseline_observation": baseline_obs,
        "samples": output_samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    document = json.loads(args.capture.read_text(encoding="utf-8"))
    result = analyze(document)
    output = args.output or args.capture.with_name(f"{args.capture.stem}_analysis.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
