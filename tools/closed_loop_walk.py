"""Move the live game one input at a time with direct runtime verification.

This is intentionally a small research/operator tool.  ``input.press`` only
acknowledges that an input was queued; every step is verified through the
direct ``/api/v1/player/runtime`` sample before another input is sent.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DIRECTIONS = {
    "Up": (0, -1),
    "Down": (0, 1),
    "Left": (-1, 0),
    "Right": (1, 0),
}


def http_json(base: str, path: str, *, method: str = "GET", body: Any = None) -> Any:
    payload = None
    headers = {"Accept": "application/json"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(base.rstrip("/") + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=8) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"request failed {path}: {exc.reason}") from exc
    return json.loads(raw.decode("utf-8")) if raw else None


def grid(player: dict[str, Any]) -> tuple[int, int, int]:
    point = ((player.get("position") or {}).get("grid") or {})
    return int(point["x"]), int(point["y"]), int(point["z"])


def compact_player(player: dict[str, Any]) -> dict[str, Any]:
    x, y, z = grid(player)
    return {
        "frame": player.get("frame"),
        "zone_id": player.get("zone_id"),
        "x": x,
        "y": y,
        "z": z,
        "facing": (player.get("orientation") or {}).get("facing"),
        "status": player.get("status"),
    }


def read_runtime(base: str) -> dict[str, Any]:
    value = http_json(base, "/api/v1/player/runtime")
    if not isinstance(value, dict):
        raise RuntimeError("player runtime response is not an object")
    return value


def read_screen(base: str) -> dict[str, Any]:
    value = http_json(base, "/api/v1/game/state")
    if not isinstance(value, dict):
        return {"status": "unresolved"}
    screen = value.get("screen") if isinstance(value.get("screen"), dict) else {}
    return {
        "status": value.get("status"),
        "can_act": value.get("can_act"),
        "screen_type": screen.get("screen_type"),
        "observation_status": screen.get("observation_status"),
    }


def press_and_sample(base: str, button: str, frames: int, before: dict[str, Any]) -> dict[str, Any]:
    http_json(base, "/api/actions/press", method="POST", body={"button": button, "frames": frames})
    # The bridge consumes queued frames asynchronously.  Poll direct RAM until
    # a fresh frame is observed, avoiding the slower RuntimeHub cache.
    before_frame = int(before.get("frame") or 0)
    deadline = time.monotonic() + 2.0
    latest = before
    while time.monotonic() < deadline:
        time.sleep(0.06)
        latest = read_runtime(base)
        if int(latest.get("frame") or 0) > before_frame:
            break
    return latest


def choose_buttons(current: tuple[int, int, int], goal: tuple[int, int, int]) -> list[str]:
    cx, _cy, cz = current
    gx, _gy, gz = goal
    preferred: list[str] = []
    if gx > cx:
        preferred.append("Right")
    elif gx < cx:
        preferred.append("Left")
    if gz > cz:
        preferred.append("Down")
    elif gz < cz:
        preferred.append("Up")
    # Try the axis with the larger remaining distance first, then the other
    # axis and finally the opposite directions as obstacle detours.
    if abs(gz - cz) > abs(gx - cx) and len(preferred) == 2:
        preferred.reverse()
    for button in DIRECTIONS:
        if button not in preferred:
            preferred.append(button)
    return preferred


def walk(args: argparse.Namespace) -> int:
    current = read_runtime(args.base)
    goal = (args.x, args.y, args.z)
    log: list[dict[str, Any]] = []
    blocked: set[tuple[tuple[int, int, int], str]] = set()
    visited: set[tuple[int, int, int]] = set()

    for step_index in range(args.max_steps + 1):
        point = grid(current)
        zone = current.get("zone_id")
        screen = read_screen(args.base)
        event: dict[str, Any] = {
            "step": step_index,
            "before": compact_player(current),
            "screen": screen,
            "goal": {"zone_id": args.zone, "x": args.x, "y": args.y, "z": args.z},
        }
        log.append(event)
        print(json.dumps(event, ensure_ascii=True), flush=True)

        if zone != args.zone:
            event["result"] = "zone_changed"
            break
        if point == goal:
            event["result"] = "goal_reached"
            break
        if screen.get("screen_type") != "OVERWORLD" or screen.get("can_act") is not True:
            event["result"] = "modal_or_unresolved"
            break
        if point in visited and step_index > 0 and step_index >= args.max_steps:
            event["result"] = "step_limit"
            break
        visited.add(point)

        moved = False
        for button in choose_buttons(point, goal):
            if (point, button) in blocked:
                continue
            after = press_and_sample(args.base, button, args.frames, current)
            after_point = grid(after)
            event.setdefault("attempts", []).append(
                {"button": button, "after": compact_player(after), "moved": after_point != point}
            )
            if after.get("zone_id") != args.zone:
                current = after
                moved = True
                break
            if after_point != point:
                current = after
                moved = True
                break
            blocked.add((point, button))
        if not moved:
            event["result"] = "no_route_from_current_tile"
            break
    else:
        log[-1]["result"] = "step_limit"

    output = Path(args.log).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"format": "black2-closed-loop-walk/v1", "events": log}, ensure_ascii=True, indent=2), encoding="utf-8")
    final = compact_player(current)
    print(json.dumps({"final": final, "log": str(output)}, ensure_ascii=True), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--zone", type=int, required=True)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--z", type=int, required=True)
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--max-steps", type=int, default=160)
    parser.add_argument("--log", default="reverse_engineering/experiments/closed_loop_walk.json")
    return walk(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
