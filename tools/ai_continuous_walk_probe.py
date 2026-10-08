#!/usr/bin/env python3
"""Probe one continuous held-direction segment through API/RAM evidence.

This is a bounded diagnostic, not a story navigator.  It sends exactly one
held button, waits for the bridge queue to drain, and records semantic state,
player coordinates, input completion, and a small RAM batch before/after.
Screenshots are only captured when explicitly requested or when a guarded
semantic mismatch occurs.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_LOG_DIR = Path("runtime/logs/continuous_walk")

RAM_RANGES = [
    {"id": "player_state", "domain": "Main RAM", "offset": 0x23B684, "length": 0x100},
    {"id": "player_actor", "domain": "Main RAM", "offset": 0x23E3E4, "length": 0x100},
    {"id": "actor_system", "domain": "Main RAM", "offset": 0x23DB68, "length": 0x200},
    {"id": "input_control", "domain": "Main RAM", "offset": 0x332B40, "length": 0x180},
]


class ApiError(RuntimeError):
    pass


def request_json(session: requests.Session, base_url: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = session.request(method, f"{base_url}{path}", timeout=kwargs.pop("timeout", 20), **kwargs)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiError(f"{method} {path} returned non-JSON HTTP {response.status_code}") from exc
    if response.status_code >= 400:
        raise ApiError(f"{method} {path} returned HTTP {response.status_code}: {json.dumps(payload, ensure_ascii=False)}")
    if isinstance(payload, dict):
        return payload
    raise ApiError(f"{method} {path} returned a non-object payload")


def capture_state(session: requests.Session, base_url: str) -> dict[str, Any]:
    current = request_json(session, base_url, "GET", "/api/v1/game/current")
    player = request_json(session, base_url, "GET", "/api/v1/player/runtime")
    input_state = request_json(session, base_url, "GET", "/api/dev/input_state")
    memory = request_json(
        session,
        base_url,
        "POST",
        "/api/dev/memory_batch_snapshot",
        json={"ranges": RAM_RANGES},
    )
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "current": current,
        "player": player,
        "input_state": input_state,
        "memory": memory,
    }


def player_position(snapshot: dict[str, Any]) -> tuple[int, int, int] | None:
    player = snapshot.get("player") or {}
    position = player.get("position") or {}
    grid = position.get("grid") or {}
    try:
        return int(grid["x"]), int(grid["y"]), int(grid["z"])
    except (KeyError, TypeError, ValueError):
        return None


def active_context(snapshot: dict[str, Any]) -> str | None:
    return (snapshot.get("current") or {}).get("primary_context")


def battle_active(snapshot: dict[str, Any]) -> bool:
    layers = (snapshot.get("current") or {}).get("layers") or []
    for layer in layers:
        if layer.get("id") == "battle":
            return bool(layer.get("active"))
    return active_context(snapshot) == "battle"


def semantic_mismatch(before: dict[str, Any], after: dict[str, Any], button: str) -> list[str]:
    problems: list[str] = []
    before_pos = player_position(before)
    after_pos = player_position(after)
    if before_pos is None or after_pos is None:
        problems.append("PLAYER_POSITION_UNRESOLVED")
    elif before_pos == after_pos:
        problems.append("HELD_INPUT_DID_NOT_CHANGE_GRID_POSITION")
    if battle_active(after):
        problems.append("BATTLE_INTERRUPTED_HELD_SEGMENT")
    if active_context(after) != "exploration":
        problems.append(f"POST_CONTEXT_UNEXPECTED:{active_context(after)}")
    before_zone = ((before.get("player") or {}).get("zone_id"))
    after_zone = ((after.get("player") or {}).get("zone_id"))
    if before_zone != after_zone:
        problems.append(f"ZONE_CHANGED:{before_zone}->{after_zone}")
    return problems


def maybe_capture(session: requests.Session, base_url: str, label: str) -> dict[str, Any] | None:
    return request_json(session, base_url, "POST", "/api/dev/capture", json={"label": label})


def run(args: argparse.Namespace) -> int:
    session = requests.Session()
    base_url = args.base_url.rstrip("/")
    before = capture_state(session, base_url)
    if active_context(before) != "exploration" or battle_active(before):
        raise ApiError("probe requires a stable overworld exploration state before input")

    press = request_json(
        session,
        base_url,
        "POST",
        "/api/actions/press",
        json={"button": args.button, "frames": args.frames},
    )
    verification = ((press.get("result") or {}).get("input_verification") or {})

    # The ActionEngine now waits on the actual emulator frame and queue state,
    # but allow a short semantic settle window for grid snapping/warps.
    settle_deadline = time.monotonic() + args.settle_seconds
    after = capture_state(session, base_url)
    while time.monotonic() < settle_deadline:
        if (after.get("input_state") or {}).get("queue_len", 0) == 0:
            break
        time.sleep(0.05)
        after = capture_state(session, base_url)

    problems = semantic_mismatch(before, after, args.button)
    if not verification.get("completed"):
        problems.append("INPUT_COMPLETION_UNVERIFIED")

    before_pos = player_position(before)
    after_pos = player_position(after)
    delta = None
    if before_pos is not None and after_pos is not None:
        delta = {
            "x": after_pos[0] - before_pos[0],
            "y": after_pos[1] - before_pos[1],
            "z": after_pos[2] - before_pos[2],
            "tiles_manhattan": abs(after_pos[0] - before_pos[0]) + abs(after_pos[2] - before_pos[2]),
        }

    record: dict[str, Any] = {
        "schema": "black2-continuous-walk-probe/v1",
        "probe": {
            "button": args.button,
            "requested_hold_frames": args.frames,
            "settle_seconds": args.settle_seconds,
            "normal_control_policy": "API/RAM first; screenshot only on explicit request or mismatch",
        },
        "result": {
            "ok": not problems,
            "problems": problems,
            "delta_grid": delta,
            "before_context": active_context(before),
            "after_context": active_context(after),
            "before_zone": ((before.get("player") or {}).get("zone_id")),
            "after_zone": ((after.get("player") or {}).get("zone_id")),
        },
        "input": press,
        "input_verification": verification,
        "before": before,
        "after": after,
    }

    if args.capture_always or problems:
        record["diagnostic_capture"] = maybe_capture(
            session,
            base_url,
            f"continuous_walk_{args.button}_{args.frames}f_{'mismatch' if problems else 'manual'}",
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output_dir / f"{stamp}_{args.button.lower()}_{args.frames}f.json"
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "result": record["result"], "input_verification": verification}, ensure_ascii=False, indent=2))
    return 0 if not problems else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--button", choices=["Up", "Down", "Left", "Right"], required=True)
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--capture-always", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
