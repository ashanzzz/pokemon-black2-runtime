#!/usr/bin/env python3
"""Evidence-first one-action runner for the Black 2 playtest.

The runner keeps the repetitive API choreography in one place:
before/after semantic state, a real BizHawk capture, one bounded RAM batch,
the exact action request/response, and a compact diff manifest.  It is
deliberately one action per invocation so an agent can re-observe before every
new input and cannot accidentally replay a stale button queue.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests


DEFAULT_RANGES = [
    {"id": "dialogue_flags", "domain": "Main RAM", "offset": 0x247540, "length": 0x10},
    {"id": "dialogue_tcb", "domain": "Main RAM", "offset": 0x332C20, "length": 0x80},
    {"id": "control", "domain": "Main RAM", "offset": 0x332B40, "length": 0x180},
    {"id": "msg", "domain": "Main RAM", "offset": 0x2490A0, "length": 0x100},
    {"id": "pixel", "domain": "Main RAM", "offset": 0x3353C0, "length": 0xF00},
]


class Api:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        url = self.base_url + path
        try:
            response = requests.request(method, url, json=body, timeout=self.timeout)
            try:
                payload = response.json()
            except ValueError:
                payload = {"text": response.text[:2000]}
            return {"status_code": response.status_code, "ok": response.ok, "payload": payload}
        except requests.RequestException as exc:
            return {"status_code": None, "ok": False, "payload": {"error": f"{type(exc).__name__}: {exc}"}}

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, body: Any = None) -> dict[str, Any]:
        return self.request("POST", path, body)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _payload(result: dict[str, Any]) -> Any:
    return result.get("payload")


def _capture(api: Api, out_dir: Path, label: str) -> dict[str, Any]:
    result = api.post("/api/dev/capture", {"label": label})
    _write_json(out_dir / "capture.response.json", result)
    capture = _payload(result)
    if not isinstance(capture, dict) or not capture.get("capture_url"):
        return {"status": "error", "response": result}
    url = api.base_url + str(capture["capture_url"])
    try:
        response = requests.get(url, timeout=api.timeout)
        response.raise_for_status()
        (out_dir / "screen.png").write_bytes(response.content)
        capture = dict(capture)
        capture["sha256"] = hashlib.sha256(response.content).hexdigest()
        capture["bytes"] = len(response.content)
        _write_json(out_dir / "capture.json", capture)
        return capture
    except requests.RequestException as exc:
        return {"status": "error", "capture": capture, "error": f"{type(exc).__name__}: {exc}"}


def _collect(
    api: Api,
    out_dir: Path,
    phase: str,
    ranges: list[dict[str, Any]],
    *,
    capture: bool = False,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    endpoints = {
        "agent": "/api/v1/agent/state",
        "current": "/api/v1/game/current",
        # This is the action verifier's primary coordinate source.  The
        # RuntimeHub/current endpoint is intentionally retained as a semantic
        # cross-check because it can lag for one cache tick after input.
        "runtime_live": "/api/v1/player/runtime",
        "runtime": "/api/v1/runtime/snapshot",
        "environment": "/api/v1/game/environment",
        "battle": "/api/v1/battle/state",
        "view_local": "/api/v1/ai/view/current?profile=local_7x7",
    }
    collected: dict[str, Any] = {}
    for name, path in endpoints.items():
        result = api.get(path)
        collected[name] = _payload(result)
        _write_json(out_dir / f"{phase}.{name}.json", result)
    memory_result = api.post("/api/dev/memory_batch_snapshot", {"ranges": ranges})
    collected["memory"] = _payload(memory_result)
    _write_json(out_dir / f"{phase}.memory.batch.json", memory_result)
    if capture:
        collected["capture"] = _capture(api, out_dir, f"{phase}_{out_dir.name}")
    return collected


def _identity(sample: dict[str, Any]) -> dict[str, Any]:
    agent = sample.get("agent") if isinstance(sample.get("agent"), dict) else {}
    current = sample.get("current") if isinstance(sample.get("current"), dict) else {}
    exploration = current.get("exploration") if isinstance(current.get("exploration"), dict) else {}
    live = sample.get("runtime_live") if isinstance(sample.get("runtime_live"), dict) else {}
    live_grid = ((live.get("position") or {}).get("grid") if isinstance(live.get("position"), dict) else {}) or {}
    live_locomotion = live.get("locomotion") if isinstance(live.get("locomotion"), dict) else {}
    live_position = {
        "x": live_grid.get("x"),
        "y": live_grid.get("y"),
        "z": live_grid.get("z"),
    }
    live_has_position = all(value is not None for value in live_position.values())
    return {
        "session_id": agent.get("session_id"),
        "frame": live.get("frame") if live.get("frame") is not None else (agent.get("frame") or current.get("frame")),
        "primary_mode": agent.get("primary_mode"),
        "primary_context": current.get("primary_context"),
        "zone_id": live.get("zone_id") if live.get("zone_id") is not None else exploration.get("zone_id"),
        "position": live_position if live_has_position else exploration.get("position"),
        "locomotion_phase": live_locomotion.get("phase"),
        "input_owner": (current.get("input") or {}).get("owner") if isinstance(current.get("input"), dict) else None,
    }


def _positions_disagree(sample: dict[str, Any]) -> bool:
    """Detect a semantic cache disagreement without using pixels as truth."""
    current = sample.get("current") if isinstance(sample.get("current"), dict) else {}
    exploration = current.get("exploration") if isinstance(current.get("exploration"), dict) else {}
    live = sample.get("runtime_live") if isinstance(sample.get("runtime_live"), dict) else {}
    live_grid = ((live.get("position") or {}).get("grid") if isinstance(live.get("position"), dict) else {}) or {}
    semantic_grid = exploration.get("position") if isinstance(exploration.get("position"), dict) else {}
    keys = ("x", "y", "z")
    if not all(key in live_grid and live_grid.get(key) is not None for key in keys):
        return True
    if not all(key in semantic_grid and semantic_grid.get(key) is not None for key in keys):
        return True
    return any(live_grid.get(key) != semantic_grid.get(key) for key in keys) or (
        live.get("zone_id") is not None and exploration.get("zone_id") is not None
        and live.get("zone_id") != exploration.get("zone_id")
    )


def _wait_for_live_frame(api: Api, before: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Prefer a fresh direct-runtime sample over a fixed post-input sleep."""
    before_frame = int(before.get("frame") or 0)
    deadline = time.monotonic() + max(0.0, timeout)
    latest = before
    while time.monotonic() < deadline:
        result = api.get("/api/v1/player/runtime")
        payload = _payload(result)
        if isinstance(payload, dict):
            latest = payload
            phase = str((payload.get("locomotion") or {}).get("phase") or "")
            if int(payload.get("frame") or 0) > before_frame and phase in {"Idle", ""}:
                return payload
        time.sleep(0.06)
    return latest


def _memory_diff(before: Any, after: Any) -> dict[str, Any]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"status": "unresolved"}
    b_results = before.get("results") if isinstance(before.get("results"), dict) else {}
    a_results = after.get("results") if isinstance(after.get("results"), dict) else {}
    result: dict[str, Any] = {"status": "ok", "ranges": {}}
    for name in sorted(set(b_results) | set(a_results)):
        b = b_results.get(name) if isinstance(b_results.get(name), dict) else {}
        a = a_results.get(name) if isinstance(a_results.get(name), dict) else {}
        bb = bytes(int(v) & 0xFF for v in (b.get("bytes") or []))
        aa = bytes(int(v) & 0xFF for v in (a.get("bytes") or []))
        changed = [index for index, (x, y) in enumerate(zip(bb, aa)) if x != y]
        result["ranges"][name] = {
            "before_length": len(bb),
            "after_length": len(aa),
            "changed_count": len(changed) + abs(len(bb) - len(aa)),
            "changed_offsets_preview": changed[:128],
            "before_sha256": hashlib.sha256(bb).hexdigest() if bb else None,
            "after_sha256": hashlib.sha256(aa).hexdigest() if aa else None,
        }
    return result


def run(args: argparse.Namespace) -> int:
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    case_dir = root / args.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    api = Api(args.base_url, args.timeout)
    ranges = json.loads(Path(args.ranges).read_text(encoding="utf-8")) if args.ranges else DEFAULT_RANGES
    if not isinstance(ranges, list):
        raise SystemExit("ranges JSON must be an array")
    manifest = {
        "format": "black2-evidence-bundle/v1",
        "case_id": args.case_id,
        "objective": args.objective,
        "action": {"method": args.method, "path": args.path, "body": args.body},
        "ranges": ranges,
        "writes_performed": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _write_json(case_dir / "manifest.json", manifest)
    before = _collect(api, case_dir / "before", "before", ranges, capture=False)
    before_live = before.get("runtime_live") if isinstance(before.get("runtime_live"), dict) else {}
    action_result = api.request(args.method, args.path, args.body)
    _write_json(case_dir / "action.request.json", {"method": args.method, "path": args.path, "body": args.body})
    _write_json(case_dir / "action.response.json", action_result)
    # A fixed sleep is retained only as a small lower bound for the bridge;
    # direct PlayerRuntime is then polled for a fresh, idle sample.
    time.sleep(max(0.0, args.wait))
    live_after = _wait_for_live_frame(api, before_live, args.settle_timeout)
    after = _collect(api, case_dir / "after", "after", ranges, capture=False)
    after["runtime_live"] = live_after
    _write_json(case_dir / "after" / "after.runtime_live.settled.json", {"status_code": 200, "ok": True, "payload": live_after})
    diff = {
        "before": _identity(before),
        "after": _identity(after),
        # Frame advancement is expected while the emulator runs.  Keep it
        # separate from the spatial/input identity used to decide whether a
        # direction actually changed the game state.
        "frame_changed": _identity(before).get("frame") != _identity(after).get("frame"),
        "position_changed": (
            _identity(before).get("zone_id"), _identity(before).get("position")
        ) != (
            _identity(after).get("zone_id"), _identity(after).get("position")
        ),
        "identity_changed": (
            _identity(before).get("session_id"), _identity(before).get("zone_id"), _identity(before).get("position")
        ) != (
            _identity(after).get("session_id"), _identity(after).get("zone_id"), _identity(after).get("position")
        ),
        "semantic_position_disagreement": _positions_disagree(after),
        "memory": _memory_diff(before.get("memory"), after.get("memory")),
        "screen_sha256": {"before": None, "after": None},
    }
    # Screens are diagnostic evidence, not the control loop.  Capture only
    # when the semantic API layers disagree, the action failed, or the caller
    # explicitly requested an always-captured bundle.
    if args.capture_always or diff["semantic_position_disagreement"] or not action_result.get("ok"):
        before_capture = _capture(api, case_dir / "before", f"before_{case_dir.name}")
        after_capture = _capture(api, case_dir / "after", f"after_{case_dir.name}")
        diff["screen_sha256"] = {
            "before": before_capture.get("sha256"),
            "after": after_capture.get("sha256"),
        }
        diff["screen_reason"] = "semantic_api_disagreement_or_action_error"
    _write_json(case_dir / "diff.json", diff)
    print(json.dumps({"case_id": args.case_id, "action_status": action_result.get("status_code"), "before": diff["before"], "after": diff["after"], "identity_changed": diff["identity_changed"]}, ensure_ascii=False, indent=2))
    return 0 if action_result.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", default="runtime/evidence/ai_playtest")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--objective", default="single controlled playtest action")
    parser.add_argument("--method", default="POST")
    parser.add_argument("--path", required=True)
    parser.add_argument("--body", type=json.loads, default=None)
    parser.add_argument("--ranges", help="JSON file containing the bounded range array")
    parser.add_argument("--wait", type=float, default=0.7)
    parser.add_argument("--settle-timeout", type=float, default=1.5)
    parser.add_argument("--capture-always", action="store_true", help="capture before/after screenshots for this case")
    parser.add_argument("--timeout", type=float, default=20.0)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
