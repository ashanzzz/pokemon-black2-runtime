#!/usr/bin/env python3
"""Event-Aligned Trace Collector and PixelData Oracle for Gen 5 Pokémon Black 2.

This tool implements the STEP 3 requirements:
1. Event-aligned per-frame snapshot capture over critical dialogue transitions.
2. PixelData oracle: 3840-byte (240x32 4bpp) raster differential analysis without OCR.
3. Synchronous alignment of TextPrinter candidate fields, control candidate windows,
   source text consumption, and raster buffer changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

API_BASE = "http://127.0.0.1:8765"

# Observation regions (Main RAM offsets)
ADDR_PRINTER_CANDIDATE = 0x31FCB0
SIZE_PRINTER_CANDIDATE = 0x80

ADDR_CONTROL_CANDIDATE = 0x332B40
SIZE_CONTROL_CANDIDATE = 0x180

ADDR_SCRIPTWORK_CONTEXT = 0x247500
SIZE_SCRIPTWORK_CONTEXT = 0x80

ADDR_MSGBUFFER = 0x2490A0
SIZE_MSGBUFFER = 0x100

ADDR_PIXELDATA = 0x3353C0
SIZE_PIXELDATA = 3840  # 240 * 32 / 2 = 3840 (0xF00)

TRACE_RANGES = [
    {"id": "printer", "domain": "Main RAM", "offset": ADDR_PRINTER_CANDIDATE, "length": SIZE_PRINTER_CANDIDATE},
    {"id": "control", "domain": "Main RAM", "offset": ADDR_CONTROL_CANDIDATE, "length": SIZE_CONTROL_CANDIDATE},
    {"id": "script", "domain": "Main RAM", "offset": ADDR_SCRIPTWORK_CONTEXT, "length": SIZE_SCRIPTWORK_CONTEXT},
    {"id": "msgbuffer", "domain": "Main RAM", "offset": ADDR_MSGBUFFER, "length": SIZE_MSGBUFFER},
    {"id": "pixeldata", "domain": "Main RAM", "offset": ADDR_PIXELDATA, "length": SIZE_PIXELDATA},
]


def parse_ucs2_string(data: bytes) -> str:
    """Parse raw bytes into a Python string, escaping Gen 5 control codes."""
    chars = []
    idx = 0
    while idx + 1 < len(data):
        val = int.from_bytes(data[idx:idx+2], "little")
        if val == 0xFFFF:
            chars.append("[EOS]")
            break
        elif val == 0xFFFE:
            chars.append("[LF]")
            idx += 2
        elif val == 0xF000:
            if idx + 5 < len(data):
                cmd = int.from_bytes(data[idx+2:idx+4], "little")
                argc = int.from_bytes(data[idx+4:idx+6], "little")
                chars.append(f"[CMD:{cmd:04X}:argc={argc}]")
                idx += 6 + argc * 2
            else:
                chars.append(f"[CMD_ERR:{val:04X}]")
                idx += 2
        else:
            try:
                chars.append(chr(val))
            except ValueError:
                chars.append(f"\\u{val:04x}")
            idx += 2
    return "".join(chars)


@dataclass
class PixelDataMetrics:
    hash_sha256: str
    non_zero_bytes: int
    changed_bytes_from_prev: int
    line0_non_zero: int  # rows 0..15 (bytes 0..1919)
    line1_non_zero: int  # rows 16..31 (bytes 1920..3839)
    active_row_count: int  # count of rows with non-zero pixels
    first_active_row: int
    last_active_row: int


def analyze_pixel_data(current_bytes: bytes, prev_bytes: Optional[bytes] = None) -> PixelDataMetrics:
    """Analyze 3840-byte 240x32 4bpp pixel surface as an authoritative ground truth oracle."""
    h = hashlib.sha256(current_bytes).hexdigest()
    non_zero = sum(1 for b in current_bytes if b != 0)

    diff_count = 0
    if prev_bytes is not None and len(prev_bytes) == len(current_bytes):
        diff_count = sum(1 for a, b in zip(current_bytes, prev_bytes) if a != b)

    # 240 pixels per row @ 4bpp = 120 bytes per row. Total 32 rows = 3840 bytes.
    row_bytes = 120
    active_rows = []
    line0_count = 0
    line1_count = 0

    for row in range(32):
        row_slice = current_bytes[row * row_bytes : (row + 1) * row_bytes]
        nz = sum(1 for b in row_slice if b != 0)
        if nz > 0:
            active_rows.append(row)
        if row < 16:
            line0_count += nz
        else:
            line1_count += nz

    return PixelDataMetrics(
        hash_sha256=h,
        non_zero_bytes=non_zero,
        changed_bytes_from_prev=diff_count,
        line0_non_zero=line0_count,
        line1_non_zero=line1_count,
        active_row_count=len(active_rows),
        first_active_row=active_rows[0] if active_rows else -1,
        last_active_row=active_rows[-1] if active_rows else -1,
    )


def decode_printer_candidate(data: bytes) -> Dict[str, Any]:
    """Decode candidate fields around 0x0231FCB0."""
    if len(data) < 0x40:
        return {}

    # +0x04: line index candidate (u16)
    line_idx = int.from_bytes(data[0x04:0x06], "little")
    # +0x08: subProcess / state candidate (u8)
    sub_proc = data[0x08]
    # +0x09: delay/speed counter candidate (u8)
    speed_counter = data[0x09]
    # +0x18: current_char_ptr (u32)
    curr_char_ptr = int.from_bytes(data[0x18:0x1C], "little")
    # +0x22: scroll_distance (u16)
    scroll_dist = int.from_bytes(data[0x22:0x24], "little")
    # +0x38: cursor_x (u16)
    cursor_x = int.from_bytes(data[0x38:0x3A], "little")
    # +0x3A: cursor_y (u16)
    cursor_y = int.from_bytes(data[0x3A:0x3C], "little")

    return {
        "line_index": line_idx,
        "sub_proc_candidate": sub_proc,
        "speed_counter_candidate": speed_counter,
        "current_char_ptr": f"0x{curr_char_ptr:08X}",
        "raw_curr_char_ptr": curr_char_ptr,
        "scroll_distance": scroll_dist,
        "cursor_x": cursor_x,
        "cursor_y": cursor_y,
    }


def capture_single_snapshot() -> Tuple[int, Dict[str, bytes]]:
    """Capture named ranges in an atomic bridge frame."""
    resp = requests.post(
        f"{API_BASE}/api/dev/memory_batch_snapshot",
        json={"ranges": TRACE_RANGES},
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    frame = payload["frame"]
    results = payload["results"]

    raw_ranges = {}
    for r in TRACE_RANGES:
        rid = r["id"]
        hex_data = results[rid]["hex"]
        raw_ranges[rid] = bytes.fromhex(hex_data)
    return frame, raw_ranges


def run_a_edge_event_trace(button: str = "A", sample_frames: int = 80) -> Dict[str, Any]:
    """Execute a single hardware A-edge capture through BizHawk and analyze frame-by-frame."""
    print(f"[Trace] Arming {button}-edge capture ({sample_frames} frames)...")
    req_body = {
        "button": button,
        "sample_frames": sample_frames,
        "ranges": TRACE_RANGES,
    }
    resp = requests.post(f"{API_BASE}/api/dev/a_edge_capture", json=req_body, timeout=10)
    resp.raise_for_status()

    # Poll status until complete
    max_wait = 15.0
    start_t = time.time()
    capture_data = None
    while time.time() - start_t < max_wait:
        st_resp = requests.get(f"{API_BASE}/api/dev/a_edge_capture", timeout=5)
        st_resp.raise_for_status()
        st_data = st_resp.json()
        if st_data.get("complete", False):
            capture_data = st_data
            break
        time.sleep(0.1)

    if not capture_data:
        raise TimeoutError(f"A-edge capture did not complete within {max_wait}s")

    samples = capture_data.get("samples", [])
    print(f"[Trace] Captured {len(samples)} frames. Analyzing event timeline...")

    timeline = []
    prev_pixel_bytes = None

    for idx, sample in enumerate(samples):
        frame = sample["frame"]
        ranges = sample["ranges"]

        # Handle ranges whether it is a dict or list
        range_dict = {}
        if isinstance(ranges, list):
            for item in ranges:
                rid = item.get("id")
                if rid:
                    range_dict[rid] = item
        elif isinstance(ranges, dict):
            range_dict = ranges

        pixel_bytes = bytes.fromhex(range_dict["pixeldata"]["hex"])
        printer_bytes = bytes.fromhex(range_dict["printer"]["hex"])
        control_bytes = bytes.fromhex(range_dict["control"]["hex"])
        script_bytes = bytes.fromhex(range_dict["script"]["hex"])
        msg_bytes = bytes.fromhex(range_dict["msgbuffer"]["hex"])

        pixel_metrics = analyze_pixel_data(pixel_bytes, prev_pixel_bytes)
        prev_pixel_bytes = pixel_bytes

        printer_dec = decode_printer_candidate(printer_bytes)
        script_active = script_bytes[0x46] if len(script_bytes) > 0x46 else -1

        # Raw bytes of printer candidate struct
        printer_hex = printer_bytes[:0x40].hex()
        control_hex = control_bytes.hex()

        # Decode the active TCBL control struct at 0x02332C20 (offset 0xE0 from 0x02332B40)
        tcbl = {}
        if len(control_bytes) >= 0xE0 + 0x40:
            tcbl_slice = control_bytes[0xE0 : 0xE0 + 0x40]
            phase = int.from_bytes(tcbl_slice[0x18:0x1C], "little")
            first_latch = int.from_bytes(tcbl_slice[0x1C:0x20], "little")
            bmpwin_ptr = int.from_bytes(tcbl_slice[0x20:0x24], "little")
            bitmap_ptr = int.from_bytes(tcbl_slice[0x24:0x28], "little")
            context_ptr = int.from_bytes(tcbl_slice[0x28:0x2C], "little")
            cursor_ptr = int.from_bytes(tcbl_slice[0x2C:0x30], "little")
            scroll_px = int.from_bytes(tcbl_slice[0x34:0x36], "little")
            cur_x = int.from_bytes(tcbl_slice[0x38:0x3A], "little")
            cur_y = int.from_bytes(tcbl_slice[0x3A:0x3C], "little")
            tcbl = {
                "phase": phase,
                "first_page_latch": first_latch,
                "bmpwin_ptr": f"0x{bmpwin_ptr:08X}",
                "bitmap_ptr": f"0x{bitmap_ptr:08X}",
                "context_ptr": f"0x{context_ptr:08X}",
                "source_cursor": f"0x{cursor_ptr:08X}",
                "scroll_px": scroll_px,
                "cursor_x": cur_x,
                "cursor_y": cur_y,
            }

        record = {
            "index": idx,
            "phase": sample.get("phase", f"frame_{idx}"),
            "frame": frame,
            "script_lock": script_active,
            "printer": printer_dec,
            "printer_head_hex": printer_hex,
            "tcbl_control": tcbl,
            "control_hex": control_hex,
            "pixel": asdict(pixel_metrics),
        }
        timeline.append(record)

    return {
        "schema": "event_aligned_trace/v1",
        "timestamp": time.time(),
        "button": button,
        "sample_count": len(timeline),
        "timeline": timeline,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Event-Aligned Dialogue Tracer & PixelData Oracle")
    parser.add_argument("--button", default="A", help="Button to press on first edge (default A)")
    parser.add_argument("--frames", type=int, default=80, help="Number of frames to sample")
    parser.add_argument("--out", type=str, help="Output JSON path")
    args = parser.parse_args()

    res = run_a_edge_event_trace(button=args.button, sample_frames=args.frames)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[Trace] Saved to {out_path}")
    else:
        print(json.dumps(res["timeline"][0], indent=2))
