#!/usr/bin/env python3
"""Plan or execute a long Black 2 route through the evidence-gated API.

The default is a read-only plan.  ``--execute`` is required to create a task;
the server still re-plans from live PlayerRuntime and verifies every landing.
This keeps long travel in one reusable command instead of spending one model
turn per tile.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

import requests


def request(base: str, method: str, path: str, body: Any = None, timeout: float = 20.0) -> dict[str, Any]:
    response = requests.request(method, base.rstrip("/") + path, json=body, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        payload = {"text": response.text[:2000]}
    return {"status_code": response.status_code, "ok": response.ok, "payload": payload}


def compact_runtime(value: dict[str, Any]) -> dict[str, Any]:
    grid = ((value.get("position") or {}).get("grid") or {})
    return {
        "frame": value.get("frame"),
        "session_id": (value.get("cache") or {}).get("session_id"),
        "zone_id": value.get("zone_id"),
        "grid": {key: grid.get(key) for key in ("x", "y", "z")},
        "phase": (value.get("locomotion") or {}).get("phase"),
        "transport_mode": (value.get("locomotion") or {}).get("transport_mode"),
    }


def parse_avoid_tiles(values: list[str]) -> list[dict[str, int]]:
    """Parse repeatable ``x,y,z`` temporary occupancy hints."""
    result: list[dict[str, int]] = []
    for value in values:
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 3:
            raise SystemExit(f"NAV_INVALID_AVOID_TILE: expected x,y,z, got {value!r}")
        try:
            x, y, z = (int(part) for part in parts)
        except ValueError as exc:
            raise SystemExit(f"NAV_INVALID_AVOID_TILE: expected integer x,y,z, got {value!r}") from exc
        result.append({"zone_id": 0, "x": x, "y": y, "z": z})
    return result


def run(args: argparse.Namespace) -> int:
    base = args.base.rstrip("/")
    runtime_result = request(base, "GET", "/api/v1/player/runtime")
    runtime = runtime_result.get("payload") if isinstance(runtime_result.get("payload"), dict) else {}
    current_result = request(base, "GET", "/api/v1/game/current")
    current = current_result.get("payload") if isinstance(current_result.get("payload"), dict) else {}
    if runtime.get("status") not in {"resolved", "candidate"}:
        raise SystemExit("NAV_PREFLIGHT_FAILED: PlayerRuntime is not resolved")
    if (runtime.get("locomotion") or {}).get("phase") != "Idle":
        raise SystemExit("NAV_PREFLIGHT_FAILED: player is not idle")
    if current.get("primary_context") not in {"exploration", "field"}:
        raise SystemExit("NAV_PREFLIGHT_FAILED: current semantic context is not exploration")

    if args.global_x is not None or args.global_y is not None or args.global_z is not None:
        if not all(value is not None for value in (args.global_x, args.global_y, args.global_z)):
            raise SystemExit("NAV_INVALID_DESTINATION: --global-x/--global-y/--global-z must be supplied together")
        destination = {
            "type": "global_grid", "space": "gen5-matrix-grid-v1",
            "x": args.global_x, "y": args.global_y, "z": args.global_z,
        }
        if args.matrix_id is not None:
            destination["matrix_id"] = args.matrix_id
    else:
        if not all(value is not None for value in (args.zone, args.x, args.y, args.z)):
            raise SystemExit("NAV_INVALID_DESTINATION: local routes require --zone/--x/--y/--z")
        destination = {
            "type": "grid", "space": "gen5-field-grid-v1", "zone_id": args.zone,
            "x": args.x, "y": args.y, "z": args.z,
        }
    avoid_tiles = parse_avoid_tiles(args.avoid)
    for tile in avoid_tiles:
        tile["zone_id"] = args.zone if args.zone is not None else 0
    plan_body = {
        "destination": destination,
        "movement_mode": args.movement_mode,
        "navigation_intent": "walk_to_tile",
        "allow_unverified_terrain": bool(args.allow_unverified_terrain),
    }
    if avoid_tiles:
        plan_body["occupancy"] = avoid_tiles
    task_body = {
        **plan_body,
        "max_steps": args.max_steps,
        "correlation_id": args.correlation_id,
    }
    context_result = request(base, "GET", "/api/v1/navigation/context")
    plan_result = request(base, "POST", "/api/v1/navigation/plans", plan_body)
    plan = plan_result.get("payload") if isinstance(plan_result.get("payload"), dict) else {}
    record: dict[str, Any] = {
        "format": "black2-ai-navigation-run/v1",
        "preflight": {"runtime": compact_runtime(runtime), "current": current, "navigation_context": context_result},
        "request": {"plan": plan_body, "task": task_body},
        "plan": plan_result,
    }
    if not args.execute:
        record["result"] = {"status": "planned_only", "execution_started": False}
    elif not plan_result.get("ok"):
        record["result"] = {"status": "plan_rejected", "execution_started": False}
    elif plan.get("resolved_start", {}).get("source") != "player_runtime":
        record["result"] = {"status": "execution_rejected", "execution_started": False, "reason": "plan start is not live PlayerRuntime"}
    else:
        task_result = request(base, "POST", "/api/v1/navigation/tasks", task_body)
        record["task"] = task_result
        task = task_result.get("payload") if isinstance(task_result.get("payload"), dict) else {}
        task_id = task.get("task_id")
        statuses = []
        if task_result.get("ok") and task_id:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                status_result = request(base, "GET", f"/api/v1/navigation/tasks/{task_id}")
                status = status_result.get("payload") if isinstance(status_result.get("payload"), dict) else {}
                statuses.append({"status_code": status_result.get("status_code"), "payload": status})
                if status.get("status") in {"succeeded", "failed", "cancelled", "stopped"}:
                    break
                time.sleep(args.poll)
        record["task_status_samples"] = statuses[-100:]
        record["result"] = {"status": (statuses[-1]["payload"].get("status") if statuses else "accepted"), "execution_started": True}

    output = Path(args.log).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": record["result"], "log": str(output), "runtime": compact_runtime(runtime), "plan": {
        "status": plan.get("status"), "route_source": plan.get("route_source"), "cost": plan.get("cost"), "movement": plan.get("movement"),
    }}, ensure_ascii=False, indent=2))
    return 0 if record["result"].get("status") in {"planned_only", "succeeded", "accepted"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--zone", type=int)
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--z", type=int)
    parser.add_argument("--global-x", type=int, help="Matrix-global destination x; use with --global-y/--global-z")
    parser.add_argument("--global-y", type=int, help="Matrix-global destination elevation")
    parser.add_argument("--global-z", type=int, help="Matrix-global destination z")
    parser.add_argument("--matrix-id", type=int, help="Optional Matrix id for a global destination")
    parser.add_argument("--movement-mode", choices=("auto", "walk", "run", "bike", "surf"), default="walk")
    parser.add_argument("--max-steps", type=int, default=2000)
    parser.add_argument("--execute", action="store_true", help="create and poll a navigation task after a successful live plan")
    parser.add_argument(
        "--allow-unverified-terrain", action="store_true",
        help="opt in to flag-clear but unverified ROM terrain candidates; every landing is still checked",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--poll", type=float, default=0.25)
    parser.add_argument("--correlation-id", default="ai-playtest-navigation")
    parser.add_argument(
        "--avoid", action="append", default=[], metavar="X,Y,Z",
        help="temporary same-Zone tile to avoid; repeat for multiple tiles",
    )
    parser.add_argument("--log", default="runtime/ai_context/navigation_run.json")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
