"""Walk a bounded same-Zone route with direct PlayerRuntime verification.

This is the small-route companion to ``ai_navigation_task.py``.  It is useful
when the ROM planner intentionally refuses an unresolved tile, but a user has
already supplied a short, evidence-backed corridor (for example, the inside
of a Pokémon Center).  Every turn and step is re-read from the live runtime;
the script stops on the first unexpected result and writes a compact log.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _json_request(base: str, method: str, path: str, body: Any = None, timeout: float = 20.0) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(f"{base.rstrip('/')}{path}", data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw else {}
            return {"status_code": response.status, "ok": 200 <= response.status < 300, "payload": payload}
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return {"status_code": error.code, "ok": False, "payload": payload}
    except (OSError, URLError, TimeoutError) as error:
        return {"status_code": None, "ok": False, "payload": {"error": f"{type(error).__name__}: {error}"}}


def _runtime(base: str, timeout: float) -> dict[str, Any]:
    response = _json_request(base, "GET", "/api/v1/player/runtime", timeout=timeout)
    return response.get("payload") if isinstance(response.get("payload"), dict) else {}


def _game_state(base: str, timeout: float) -> dict[str, Any]:
    response = _json_request(base, "GET", "/api/v1/game/current", timeout=timeout)
    return response.get("payload") if isinstance(response.get("payload"), dict) else {}


def _grid(player: dict[str, Any]) -> dict[str, int] | None:
    position = player.get("position") if isinstance(player.get("position"), dict) else {}
    grid = position.get("grid") if isinstance(position.get("grid"), dict) else {}
    if not isinstance(player.get("zone_id"), int) or not all(isinstance(grid.get(k), int) for k in ("x", "y", "z")):
        return None
    return {"zone_id": int(player["zone_id"]), **{k: int(grid[k]) for k in ("x", "y", "z")}}


def _facing(player: dict[str, Any]) -> str | None:
    orientation = player.get("orientation") if isinstance(player.get("orientation"), dict) else {}
    value = orientation.get("facing")
    return str(value) if isinstance(value, str) else None


def _next_direction(current: dict[str, int], target: dict[str, int]) -> str | None:
    if current["x"] < target["x"]:
        return "Right"
    if current["x"] > target["x"]:
        return "Left"
    if current["z"] < target["z"]:
        return "Down"
    if current["z"] > target["z"]:
        return "Up"
    return None


def _facing_for(button: str) -> str:
    return {"Up": "North", "Down": "South", "Left": "West", "Right": "East"}[button]


def _capture_on_mismatch(base: str, label: str, timeout: float) -> dict[str, Any]:
    capture = _json_request(base, "POST", "/api/dev/capture", {"label": label}, timeout=timeout)
    # The endpoint intentionally rejects an empty range list.  Keep this
    # reusable walker self-contained, but export a small, fixed-frame RAM
    # bundle whenever a turn/step diverges so another agent can inspect the
    # raw state without repeating the input.
    ranges = [
        {"id": "dialogue_flags", "domain": "Main RAM", "offset": 0x247540, "length": 0x10},
        {"id": "actor_system_and_player", "domain": "Main RAM", "offset": 0x23DB68, "length": 0x500},
        {"id": "control_candidate", "domain": "Main RAM", "offset": 0x332B40, "length": 0x180},
    ]
    memory = _json_request(base, "POST", "/api/dev/memory_batch_snapshot", {"ranges": ranges}, timeout=timeout)
    return {"capture": capture, "memory": memory}


def run(args: argparse.Namespace) -> int:
    target = {"zone_id": args.zone, "x": args.x, "y": args.y, "z": args.z}
    out_path = Path(args.log)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "format": "black2-verified-walk/v1",
        "target": target,
        "writes_performed": False,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "actions": [],
        "status": "running",
    }

    def save() -> None:
        out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    game = _game_state(args.base, args.timeout)
    exploration = game.get("exploration") if isinstance(game.get("exploration"), dict) else {}
    battle = game.get("battle") if isinstance(game.get("battle"), dict) else {}
    dialogue = game.get("dialogue") if isinstance(game.get("dialogue"), dict) else {}
    transition = game.get("transition") if isinstance(game.get("transition"), dict) else {}
    record["preflight"] = {
        "primary_context": game.get("primary_context"),
        "exploration_active": exploration.get("active"),
        "can_move": exploration.get("can_move"),
        "battle_active": battle.get("active"),
        "dialogue_active": dialogue.get("active"),
        "transition_active": transition.get("active"),
    }
    if (
        not game
        or game.get("primary_context") != "exploration"
        or exploration.get("can_move") is not True
        or battle.get("active") is True
        or dialogue.get("active") is True
        or transition.get("active") is True
    ):
        record.update(status="failed", stop_reason={"code": "WALK_PRECONDITION_BLOCKED", "game": game})
        save()
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 1

    for index in range(max(1, args.max_actions)):
        live_game = _game_state(args.base, args.timeout)
        live_exploration = live_game.get("exploration") if isinstance(live_game.get("exploration"), dict) else {}
        live_battle = live_game.get("battle") if isinstance(live_game.get("battle"), dict) else {}
        live_dialogue = live_game.get("dialogue") if isinstance(live_game.get("dialogue"), dict) else {}
        live_transition = live_game.get("transition") if isinstance(live_game.get("transition"), dict) else {}
        if (
            not live_game
            or live_game.get("primary_context") != "exploration"
            or live_exploration.get("can_move") is not True
            or live_battle.get("active") is True
            or live_dialogue.get("active") is True
            or live_transition.get("active") is True
        ):
            record.update(
                status="failed",
                stop_reason={"code": "WALK_RUNTIME_BLOCKED", "index": index, "game": live_game},
                final=_grid(_runtime(args.base, args.timeout)),
            )
            save()
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 1
        before = _runtime(args.base, args.timeout)
        before_grid = _grid(before)
        if before_grid is None:
            record.update(status="failed", stop_reason={"code": "WALK_PLAYER_UNRESOLVED", "player": before})
            save()
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 1
        if before_grid == target:
            record.update(status="succeeded", completed_actions=index, final=before_grid)
            save()
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        if before_grid["zone_id"] != target["zone_id"]:
            record.update(status="failed", stop_reason={"code": "WALK_CROSS_ZONE_UNEXPECTED", "before": before_grid, "target": target})
            save()
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 1
        if before_grid["y"] != target["y"] and not args.allow_layer_transition:
            record.update(status="failed", stop_reason={"code": "WALK_TARGET_LAYER_MISMATCH", "before": before_grid, "target": target})
            save()
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 1

        button = _next_direction(before_grid, target)
        if button is None:
            record.update(status="failed", stop_reason={"code": "WALK_TARGET_LAYER_MISMATCH", "before": before_grid, "target": target})
            save()
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 1

        action = {"index": index, "button": button, "expected_grid": {**before_grid}}
        expected_facing = _facing_for(button)
        facing = _facing(before)
        if facing != expected_facing:
            action["kind"] = "turn"
            action["expected_facing"] = expected_facing
            record["writes_performed"] = True
            result = _json_request(args.base, "POST", "/api/actions/press", {"button": button, "frames": args.frames}, args.timeout)
            action["response"] = result
            time.sleep(max(0.0, args.wait))
            after = _runtime(args.base, args.timeout)
            action["after"] = {"grid": _grid(after), "facing": _facing(after), "frame": after.get("frame")}
            if not result.get("ok") or _grid(after) != before_grid or _facing(after) != expected_facing:
                action["evidence"] = _capture_on_mismatch(args.base, f"{args.case_prefix}_{index}_turn_mismatch", args.timeout)
                record["actions"].append(action)
                record.update(status="failed", stop_reason={"code": "WALK_TURN_MISMATCH", "action": action})
                save()
                print(json.dumps(record, ensure_ascii=False, indent=2))
                return 1
            record["actions"].append(action)
            save()
            continue

        expected = dict(before_grid)
        if button == "Left":
            expected["x"] -= 1
        elif button == "Right":
            expected["x"] += 1
        elif button == "Up":
            expected["z"] -= 1
        else:
            expected["z"] += 1
        action["kind"] = "step"
        action["expected_grid"] = expected
        record["writes_performed"] = True
        result = _json_request(args.base, "POST", "/api/actions/press", {"button": button, "frames": args.frames}, args.timeout)
        action["response"] = result
        time.sleep(max(0.0, args.wait))
        after = _runtime(args.base, args.timeout)
        action["after"] = {"grid": _grid(after), "facing": _facing(after), "frame": after.get("frame")}
        after_grid = _grid(after)
        layer_transition = (
            args.allow_layer_transition
            and after_grid is not None
            and after_grid["zone_id"] == expected["zone_id"]
            and after_grid["x"] == expected["x"]
            and after_grid["z"] == expected["z"]
            and after_grid["y"] != expected["y"]
        )
        if layer_transition:
            action["kind"] = "vertical_transition"
            action["layer_transition"] = {
                "expected_layer": expected["y"],
                "observed_layer": after_grid["y"],
                "accepted_by_flag": True,
            }
            record["actions"].append(action)
            save()
            if after_grid == target:
                record.update(status="succeeded", completed_actions=index + 1, final=after_grid)
                save()
                print(json.dumps(record, ensure_ascii=False, indent=2))
                return 0
            continue
        if not result.get("ok") or after_grid != expected:
            action["evidence"] = _capture_on_mismatch(args.base, f"{args.case_prefix}_{index}_step_mismatch", args.timeout)
            record["actions"].append(action)
            record.update(status="failed", stop_reason={"code": "WALK_STEP_MISMATCH", "action": action})
            save()
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 1
        record["actions"].append(action)
        save()
        if after_grid == target:
            record.update(status="succeeded", completed_actions=index + 1, final=after_grid)
            save()
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0

    record.update(status="failed", stop_reason={"code": "WALK_ACTION_LIMIT", "max_actions": args.max_actions}, final=_grid(_runtime(args.base, args.timeout)))
    save()
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--zone", type=int, required=True)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--z", type=int, required=True)
    parser.add_argument("--max-actions", type=int, default=64)
    parser.add_argument("--frames", type=int, default=4)
    parser.add_argument("--wait", type=float, default=0.45)
    parser.add_argument(
        "--allow-layer-transition",
        action="store_true",
        help="Accept a verified same-zone x/z landing when the game changes the live elevation layer.",
    )
    parser.add_argument("--case-prefix", default="verified_walk")
    parser.add_argument("--log", default="runtime/ai_context/verified_walk.json")
    parser.add_argument("--timeout", type=float, default=20.0)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
