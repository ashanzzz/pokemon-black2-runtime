#!/usr/bin/env python3
"""Capture a bounded NPC battle before/after observation.

The default mode is read-only.  ``--execute`` may run the existing semantic
NPC interaction task, but it never sends a battle command.  If a battle starts,
the task is observed and the script stops at the battle boundary.  The script
then stores status/RAM fields through the backend record endpoint so repeated
probes cost one reusable call sequence instead of a model turn per endpoint.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

import requests


def request(base: str, method: str, path: str, body: Any = None, timeout: float = 20.0) -> dict[str, Any]:
    try:
        response = requests.request(method, base.rstrip("/") + path, json=body, timeout=timeout)
        try:
            payload = response.json()
        except ValueError:
            payload = {"text": response.text[:2000]}
        return {"status_code": response.status_code, "ok": response.ok, "payload": payload}
    except requests.RequestException as exc:
        return {"status_code": None, "ok": False, "payload": {"error": f"{type(exc).__name__}: {exc}"}}


def payload(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("payload")
    return value if isinstance(value, dict) else {}


def collect(base: str, zone: int, timeout: float) -> dict[str, Any]:
    paths = {
        "runtime": "/api/v1/player/runtime",
        "actors": "/api/v1/lab/actors/live",
        "npc_status": f"/api/v1/ai/map/npc-battle-status?zone_id={zone}",
        "current": "/api/v1/game/current",
        "state": "/api/state",
        "battle": "/api/v1/battle/state",
        "battle_identity": "/api/v1/battle/identity",
        "flags": "/api/v1/game/flags",
        "events": "/api/v1/agent/events/log?limit=20",
    }
    result: dict[str, Any] = {}
    for name, path in paths.items():
        response = request(base, "GET", path, timeout=timeout)
        result[name] = payload(response)
        result.setdefault("http", {})[name] = {"status_code": response.get("status_code"), "ok": response.get("ok")}
    return result


def dialogue_view(result: dict[str, Any]) -> dict[str, Any]:
    """Merge current dialogue and the unresolved-printer fallback text."""
    current = result.get("current") if isinstance(result.get("current"), dict) else {}
    dialogue = current.get("dialogue") if isinstance(current.get("dialogue"), dict) else {}
    state = result.get("state") if isinstance(result.get("state"), dict) else {}
    context = state.get("context") if isinstance(state.get("context"), dict) else {}
    merged = dict(dialogue)
    if context.get("is_dialogue_active") is True:
        merged["active"] = True
    loaded = (
        merged.get("full_text") or merged.get("text")
        or context.get("full_dialogue_text")
        or context.get("loaded_dialogue_text")
    )
    if loaded:
        merged["loaded_text"] = str(loaded)[:4000]
        if not merged.get("full_text"):
            merged["full_text"] = str(loaded)[:4000]
    if context.get("screen_type") and not merged.get("screen_type"):
        merged["screen_type"] = context.get("screen_type")
    if context.get("recommended_action") and not merged.get("recommended_action"):
        merged["recommended_action"] = context.get("recommended_action")
    return merged


def compact(result: dict[str, Any]) -> dict[str, Any]:
    status = result.get("npc_status") if isinstance(result.get("npc_status"), dict) else {}
    runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
    position = ((runtime.get("position") or {}).get("grid") if isinstance(runtime.get("position"), dict) else {}) or {}
    battle = result.get("battle") if isinstance(result.get("battle"), dict) else {}
    return {
        "frame": runtime.get("frame"),
        "zone_id": runtime.get("zone_id"),
        "grid": {key: position.get(key) for key in ("x", "y", "z")},
        "battle_active": battle.get("active"),
        "npc_count": len(status.get("npcs") or []),
        "probe_candidates": status.get("probe_candidates", []),
    }


def target_npc_id(status: dict[str, Any], x: int | None, y: int | None, z: int | None) -> str | None:
    if not isinstance(status, dict) or None in (x, y, z):
        return None
    for row in status.get("npcs") or []:
        coordinate = row.get("coordinate") if isinstance(row, dict) else None
        if isinstance(coordinate, dict) and (coordinate.get("x"), coordinate.get("y"), coordinate.get("z")) == (x, y, z):
            value = row.get("npc_id")
            return str(value) if value is not None else None
    return None


def wait_for_modal_observation(base: str, timeout: float, poll: float, settle: float) -> dict[str, Any]:
    """Wait for a delayed battle/dialogue layer before taking the after sample.

    Navigation can report a verified arrival before the field script has
    published its first TextPrinter/battle frame.  A single fixed sleep then
    mislabels a delayed NPC message as ``no_battle_transition_observed``.
    This bounded poll uses only semantic endpoints and returns a small trace;
    it never sends input.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    trace: list[dict[str, Any]] = []
    detected = "none"
    while time.monotonic() < deadline:
        current_response = request(base, "GET", "/api/v1/game/current", timeout=20.0)
        battle_response = request(base, "GET", "/api/v1/battle/state", timeout=20.0)
        current = payload(current_response)
        battle = payload(battle_response)
        dialogue = current.get("dialogue") if isinstance(current.get("dialogue"), dict) else {}
        sample = {
            "at": time.time(),
            "dialogue_active": dialogue.get("active"),
            "battle_active": battle.get("active"),
            "dialogue_loaded_text": bool(dialogue.get("full_text") or dialogue.get("text")),
            "http": {
                "current": current_response.get("status_code"),
                "battle": battle_response.get("status_code"),
            },
        }
        trace.append(sample)
        if battle.get("active") is True:
            detected = "battle"
            break
        if dialogue.get("active") is True:
            detected = "dialogue"
            break
        time.sleep(max(0.05, float(poll)))
    if detected != "none" and settle > 0:
        time.sleep(float(settle))
    return {
        "detected": detected,
        "samples": trace[-20:],
        "timeout_seconds": max(0.0, float(timeout)),
        "settle_seconds": max(0.0, float(settle)) if detected != "none" else 0.0,
    }


def run(args: argparse.Namespace) -> int:
    base = args.base.rstrip("/")
    before = collect(base, args.zone, args.timeout)
    record: dict[str, Any] = {
        "format": "black2-npc-battle-probe/v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "zone_id": args.zone,
        "target": {"x": args.x, "y": args.y, "z": args.z},
        "execute": bool(args.execute),
        "writes_performed": bool(args.execute),
        "game_input_performed": bool(args.execute),
        "before": before,
    }
    probe_status = "read_only"
    probe_failure: dict[str, Any] | None = None
    if args.execute:
        if not all(value is not None for value in (args.x, args.y, args.z)):
            raise SystemExit("--execute requires --x/--y/--z")
        body = {
            "target": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": args.zone,
                       "x": args.x, "y": args.y, "z": args.z},
            "movement_mode": args.movement_mode,
            "max_steps": args.max_steps,
            "auto_dialogue": bool(args.auto_dialogue),
            "max_dialogue_steps": args.max_dialogue_steps,
            "choice_policy": "stop",
            "correlation_id": args.correlation_id,
        }
        started = request(base, "POST", "/api/v1/agent/automation/interact", body, args.timeout)
        record["automation_start"] = started
        task_id = payload(started).get("task_id")
        samples: list[dict[str, Any]] = []
        if not started.get("ok") or not task_id:
            probe_status = "start_failed"
            probe_failure = {
                "http_status": started.get("status_code"),
                "response": payload(started),
            }
        if started.get("ok") and task_id:
            deadline = time.monotonic() + args.task_timeout
            terminal_seen = False
            while time.monotonic() < deadline:
                status = request(base, "GET", f"/api/v1/agent/automation/tasks/{task_id}", timeout=args.timeout)
                status_payload = payload(status)
                samples.append({"status_code": status.get("status_code"), "payload": status_payload})
                battle = before.get("battle") if isinstance(before.get("battle"), dict) else {}
                current = status_payload.get("status")
                if current in {"succeeded", "failed", "cancelled", "stopped"} or battle.get("active") is True:
                    terminal_seen = True
                    break
                time.sleep(args.poll)
            if samples:
                final_payload = samples[-1].get("payload") if isinstance(samples[-1], dict) else {}
                final_status = final_payload.get("status") if isinstance(final_payload, dict) else None
                if final_status in {"failed", "cancelled", "stopped"}:
                    probe_status = "failed" if final_status == "failed" else str(final_status)
                    probe_failure = {
                        "task_status": final_status,
                        "phase": final_payload.get("phase"),
                        "stop_reason": final_payload.get("stop_reason"),
                    }
                elif terminal_seen:
                    probe_status = "completed"
                else:
                    probe_status = "timeout"
                    probe_failure = {
                        "task_status": final_status,
                        "phase": final_payload.get("phase"),
                        "reason": "task_timeout",
                    }
        record["automation_samples"] = samples[-100:]
    record["probe_execution"] = {
        "status": probe_status,
        "failure": probe_failure,
    }
    record["post_probe_observation"] = wait_for_modal_observation(
        base, args.observe_timeout, args.poll, args.settle
    )
    after = collect(base, args.zone, args.timeout)
    record["after"] = after
    record["summary"] = {"before": compact(before), "after": compact(after)}
    session_id = ((after.get("runtime") or {}).get("cache") or {}).get("session_id")
    target_id = target_npc_id(before.get("npc_status") or {}, args.x, args.y, args.z)
    saved = request(base, "POST", "/api/v1/ai/map/npc-battle-status/record", {
        "before": before.get("npc_status") or {},
        "after": after.get("npc_status") or {},
        "npc_ids": [target_id] if target_id else None,
        "battle_before": before.get("battle") or {},
        "battle_after": after.get("battle") or {},
        "dialogue_before": dialogue_view(before),
        "dialogue_after": dialogue_view(after),
        "session_id": session_id,
        "operation": "ai_npc_battle_probe",
        "note": args.note,
        "probe_status": probe_status,
        "probe_failure": probe_failure,
    }, args.timeout)
    record["journal_record"] = saved
    output = Path(args.log).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": record["summary"], "log": str(output),
                      "journal_status": saved.get("status_code"), "automation_started": bool(args.execute)},
                     ensure_ascii=False, indent=2))
    return 0 if saved.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--zone", type=int, required=True)
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--z", type=int)
    parser.add_argument("--execute", action="store_true", help="run semantic interaction; never sends battle commands")
    parser.add_argument("--movement-mode", choices=("auto", "walk", "run", "bike", "surf"), default="walk")
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--auto-dialogue", action="store_true", default=False)
    parser.add_argument("--max-dialogue-steps", type=int, default=64)
    parser.add_argument("--task-timeout", type=float, default=90.0)
    parser.add_argument("--poll", type=float, default=0.25)
    parser.add_argument("--settle", type=float, default=0.8)
    parser.add_argument(
        "--observe-timeout", type=float, default=5.0,
        help="bounded semantic wait for delayed battle/dialogue after arrival",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--correlation-id", default="ai-npc-battle-probe")
    parser.add_argument("--note", default=None)
    parser.add_argument("--log", default="runtime/evidence/npc_battle_probe/latest.json")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
