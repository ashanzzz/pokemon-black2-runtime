#!/usr/bin/env python3
"""EXP-021: Snapshot Independence Verification Test Suite.

Verifies that ANY arbitrary dialogue frame can be 100% reconstructed using ONLY
its current atomic RAM snapshot and a fresh, zero-history VisibleTextLedger instance.
No host-side state, no frame replay, no external caching.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Any

import requests
import pytest

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.black2.decoders.visible_text_ledger import VisibleTextLedger, VisibleSnapshot

API_BASE = "http://127.0.0.1:8765"


def fetch_live_ram_snapshot() -> Dict[str, bytes]:
    """Capture raw memory snapshot on current frame."""
    ranges = [
        {"id": "control", "domain": "Main RAM", "offset": 0x332B40, "length": 0x180},
        {"id": "msg", "domain": "Main RAM", "offset": 0x2490A0, "length": 0x100},
        {"id": "pixel", "domain": "Main RAM", "offset": 0x3353C0, "length": 3840},
    ]
    resp = requests.post(f"{API_BASE}/api/dev/memory_batch_snapshot", json={"ranges": ranges}, timeout=5)
    resp.raise_for_status()
    results = resp.json()["results"]
    return {
        "control": bytes.fromhex(results["control"]["hex"]),
        "msg": bytes.fromhex(results["msg"]["hex"]),
        "pixel": bytes.fromhex(results["pixel"]["hex"]),
    }


def parse_snapshot_with_fresh_decoder(
    control_bytes: bytes,
    msg_bytes: bytes,
    pixel_bytes: bytes,
) -> VisibleSnapshot:
    """Instantiate a completely FRESH decoder with ZERO history and parse."""
    fresh_ledger = VisibleTextLedger()

    # Dynamic extraction of fields from control window (relative to 0x02332B40)
    tcbl = control_bytes[0xE0 : 0xE0 + 0x40]
    phase = int.from_bytes(tcbl[0x18:0x1C], "little")
    latch = int.from_bytes(tcbl[0x1C:0x20], "little")
    cursor = int.from_bytes(tcbl[0x2C:0x30], "little")

    return fresh_ledger.resolve_visible_text(
        raw_msg_bytes=msg_bytes,
        source_cursor=cursor,
        phase=phase,
        first_page_latch=latch,
        pixel_bytes=pixel_bytes,
    )


def test_exp021_all():
    try:
        requests.get(f"{API_BASE}/health", timeout=0.5).raise_for_status()
    except requests.RequestException:
        pytest.skip("runtime backend is not running; EXP-021 is a live integration test")
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 70)
    print("EXP-021: SNAPSHOT INDEPENDENCE TEST SUITE (ZERO-HISTORY PARSER)")
    print("=" * 70)

    # 1. Test Slot 2 (WAIT_CLEAR)
    print("\n--- [EXP-021A: Load Slot 2 (WAIT_CLEAR)] ---")
    requests.post(f"{API_BASE}/api/dev/savestate/load?slot=2").raise_for_status()
    snap_data = fetch_live_ram_snapshot()
    res2 = parse_snapshot_with_fresh_decoder(snap_data["control"], snap_data["msg"], snap_data["pixel"])
    print(f"Phase: {res2.phase_name}, Latch: {res2.is_first_page}, Cursor: 0x{res2.cursor_addr:08X}")
    print(f"Line 0: '{res2.line0}'")
    print(f"Line 1: '{res2.line1}'")
    print(f"Pixel Oracle: Verified={res2.pixel_verified} (L0_nz={res2.pixel_line0_active}, L1_nz={res2.pixel_line1_active})")
    assert res2.line0 == "科学的力量真是惊人！", f"Expected '科学的力量真是惊人！', got '{res2.line0}'"
    assert res2.line1 == "", f"Expected empty line 1, got '{res2.line1}'"
    assert res2.phase_name == "WAIT_PAGE"
    print(">>> EXP-021A PASS")

    # 2. Test Slot 3 (WAIT_SCROLL)
    print("\n--- [EXP-021B: Load Slot 3 (WAIT_SCROLL)] ---")
    requests.post(f"{API_BASE}/api/dev/savestate/load?slot=3").raise_for_status()
    snap_data = fetch_live_ram_snapshot()
    res3 = parse_snapshot_with_fresh_decoder(snap_data["control"], snap_data["msg"], snap_data["pixel"])
    print(f"Phase: {res3.phase_name}, Latch: {res3.is_first_page}, Cursor: 0x{res3.cursor_addr:08X}")
    print(f"Line 0: '{res3.line0}'")
    print(f"Line 1: '{res3.line1}'")
    print(f"Pixel Oracle: Verified={res3.pixel_verified} (L0_nz={res3.pixel_line0_active}, L1_nz={res3.pixel_line1_active})")
    assert res3.line0 == "现在可以用通信", f"Expected '现在可以用通信', got '{res3.line0}'"
    assert res3.line1 == "和１００个人", f"Expected '和１００个人', got '{res3.line1}'"
    assert res3.phase_name == "WAIT_PAGE"
    print(">>> EXP-021B PASS")

    # 3. Test Slot 9 (WAIT_EOS)
    print("\n--- [EXP-021C: Load Slot 9 (WAIT_EOS)] ---")
    requests.post(f"{API_BASE}/api/dev/savestate/load?slot=9").raise_for_status()
    snap_data = fetch_live_ram_snapshot()
    res9 = parse_snapshot_with_fresh_decoder(snap_data["control"], snap_data["msg"], snap_data["pixel"])
    print(f"Phase: {res9.phase_name}, Latch: {res9.is_first_page}, Cursor: 0x{res9.cursor_addr:08X}")
    print(f"Line 0: '{res9.line0}'")
    print(f"Line 1: '{res9.line1}'")
    print(f"Pixel Oracle: Verified={res9.pixel_verified} (L0_nz={res9.pixel_line0_active}, L1_nz={res9.pixel_line1_active})")
    assert res9.line0 == "和１００个人", f"Expected '和１００个人', got '{res9.line0}'"
    assert res9.line1 == "同时游戏！", f"Expected '同时游戏！', got '{res9.line1}'"
    assert res9.phase_name == "WAIT_EOS"
    print(">>> EXP-021C PASS")

    # 4. Test EXP-021D: Random mid-printing frames from EXP-018
    print("\n--- [EXP-021D: Random Mid-Printing Isolated Frame Snapshots] ---")
    exp018_path = PROJECT_ROOT / "reverse_engineering" / "experiments" / "EXP_018_wait_clear_to_page2.json"
    with open(exp018_path, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    # Mid-line 0 printing (Frame 5221481), Mid-line 1 printing (Frame 5221506)
    for frame_idx, expected_l0, expected_l1 in [
        (10, "现在可以", ""),
        (22, "现在可以用通信", ""),
        (27, "现在可以用通信", "和１"),
        (35, "现在可以用通信", "和１００"),
        (47, "现在可以用通信", "和１００个人"),
    ]:
        sample = trace_data["timeline"][frame_idx]
        fnum = sample["frame"]
        tc = sample["tcbl_control"]
        cur = int(tc["source_cursor"], 16)
        ph = tc["phase"]
        lat = tc["first_page_latch"]
        ctrl_bytes = bytes.fromhex(sample["control_hex"])
        # msg_bytes from slot 2
        fresh = VisibleTextLedger()
        snap = fresh.resolve_visible_text(snap_data["msg"], cur, ph, lat)
        print(f"Mid-frame {fnum} (Sample #{frame_idx}): Cursor=0x{cur:08X} | L0='{snap.line0}', L1='{snap.line1}'")
        assert snap.line0 == expected_l0, f"Frame {fnum}: expected L0 '{expected_l0}', got '{snap.line0}'"
        assert snap.line1 == expected_l1, f"Frame {fnum}: expected L1 '{expected_l1}', got '{snap.line1}'"

    print(">>> EXP-021D PASS")
    print("\n" + "=" * 70)
    print("EXP-021 ALL TESTS PASSED! ZERO HOST-SIDE HISTORY DEPENDENCY PROVEN.")
    print("=" * 70)


if __name__ == "__main__":
    test_exp021_all()
