#!/usr/bin/env python3
"""One-action battle reverse-engineering probe.

The probe is deliberately separate from the semantic battle executor.  It
records the exact action, battle-layer observations, bounded RAM windows and
the post-action result in one auditable bundle.  A screenshot is optional and
is intended for calibration or an unresolved semantic mismatch, never as the
normal control loop.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests


DEFAULT_RANGES_PATH = Path("runtime/evidence/battle_ram_ranges.json")


class Api:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        try:
            response = requests.request(
                method,
                self.base_url + path,
                json=body,
                timeout=self.timeout,
            )
            try:
                payload = response.json()
            except ValueError:
                payload = {"text": response.text[:4000]}
            return {
                "status_code": response.status_code,
                "ok": response.ok,
                "payload": payload,
            }
        except requests.RequestException as exc:
            return {
                "status_code": None,
                "ok": False,
                "payload": {"error": f"{type(exc).__name__}: {exc}"},
            }

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, body: Any = None) -> dict[str, Any]:
        return self.request("POST", path, body)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def payload(result: dict[str, Any]) -> Any:
    return result.get("payload")


def capture(api: Api, directory: Path, label: str) -> dict[str, Any]:
    result = api.post("/api/dev/capture", {"label": label})
    write_json(directory / "capture.response.json", result)
    info = payload(result)
    if not isinstance(info, dict) or not info.get("capture_url"):
        return {"status": "error", "response": result}
    try:
        response = requests.get(api.base_url + str(info["capture_url"]), timeout=api.timeout)
        response.raise_for_status()
        raw = response.content
        (directory / "screen.png").write_bytes(raw)
        result_info = dict(info)
        result_info.update({
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
        write_json(directory / "capture.json", result_info)
        return result_info
    except requests.RequestException as exc:
        return {
            "status": "error",
            "capture": info,
            "error": f"{type(exc).__name__}: {exc}",
        }


def collect(api: Api, directory: Path, phase: str, ranges: list[dict[str, Any]]) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "current": "/api/v1/game/current",
        "layers": "/api/v1/game/layers",
        "agent": "/api/v1/agent/state",
        "battle_state": "/api/v1/battle/state",
        "battle_request": "/api/v1/battle/request",
        "battle_evidence": "/api/v1/battle/evidence",
        "party": "/api/v1/battle/party",
        "moves": "/api/v1/battle/moves?actor=player:0",
        "input_state": "/api/dev/input_state",
    }
    result: dict[str, Any] = {}
    for name, path in paths.items():
        response = api.get(path)
        result[name] = payload(response)
        write_json(directory / f"{phase}.{name}.json", response)
    memory_response = api.post("/api/dev/memory_batch_snapshot", {"ranges": ranges})
    result["memory"] = payload(memory_response)
    write_json(directory / f"{phase}.memory.batch.json", memory_response)
    return result


def _bytes(row: Any) -> bytes:
    if not isinstance(row, dict) or not isinstance(row.get("bytes"), list):
        return b""
    try:
        return bytes(int(item) & 0xFF for item in row["bytes"])
    except (TypeError, ValueError):
        return b""


def memory_diff(before: Any, after: Any) -> dict[str, Any]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"status": "unresolved", "ranges": {}}
    before_rows = before.get("results") if isinstance(before.get("results"), dict) else {}
    after_rows = after.get("results") if isinstance(after.get("results"), dict) else {}
    diff: dict[str, Any] = {"status": "ok", "frame_before": before.get("frame"), "frame_after": after.get("frame"), "ranges": {}}
    for name in sorted(set(before_rows) | set(after_rows)):
        left = _bytes(before_rows.get(name))
        right = _bytes(after_rows.get(name))
        changed = [index for index, (x, y) in enumerate(zip(left, right)) if x != y]
        diff["ranges"][name] = {
            "before_length": len(left),
            "after_length": len(right),
            "changed_count": len(changed) + abs(len(left) - len(right)),
            "changed_offsets_preview": changed[:256],
            "before_sha256": hashlib.sha256(left).hexdigest() if left else None,
            "after_sha256": hashlib.sha256(right).hexdigest() if right else None,
        }
    return diff


def _battle_identity(sample: dict[str, Any]) -> dict[str, Any]:
    state = sample.get("battle_state") if isinstance(sample.get("battle_state"), dict) else {}
    evidence = sample.get("battle_evidence") if isinstance(sample.get("battle_evidence"), dict) else {}
    request = sample.get("battle_request") if isinstance(sample.get("battle_request"), dict) else {}
    return {
        # The bridge frame changes on every read.  It is recorded in the raw
        # evidence, but must not make a read-only no-op look like a menu
        # transition.
        "active": state.get("active"),
        "active_status": state.get("active_status"),
        "phase": state.get("phase"),
        "menu": state.get("menu"),
        "available_actions": state.get("available_actions"),
        "execution_available": state.get("execution_available"),
        "request_status": request.get("status"),
        "request_id": request.get("request_id"),
        "waiting_for_player": request.get("waiting_for_player"),
        "field_busy": evidence.get("field_busy"),
        "party": sample.get("party"),
        "moves": sample.get("moves"),
    }


def run(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    case_dir = output / args.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    ranges_path = Path(args.ranges) if args.ranges else DEFAULT_RANGES_PATH
    ranges = json.loads(ranges_path.read_text(encoding="utf-8"))
    if not isinstance(ranges, list) or not ranges:
        raise SystemExit("battle ranges JSON must be a non-empty array")

    api = Api(args.base_url, args.timeout)
    manifest = {
        "format": "black2-battle-reverse-probe/v1",
        "case_id": args.case_id,
        "objective": args.objective,
        "action": {"method": args.method, "path": args.path, "body": args.body},
        "ranges": ranges,
        "writes_performed": False,
        "screenshot_policy": {
            "captured": bool(args.capture_always),
            "reason": "explicit_calibration" if args.capture_always else "semantic_battle_probe_only",
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_json(case_dir / "manifest.json", manifest)

    before_capture: dict[str, Any] | None = None
    if args.capture_always:
        # Capture before the action, not after both reads.  This keeps the
        # screenshot pair aligned with the semantic/RAM pair and is useful
        # when a menu transition is the calibration target.
        before_capture = capture(api, case_dir / "before", f"before_{args.case_id}")
    before = collect(api, case_dir / "before", "before", ranges)
    action = api.request(args.method, args.path, args.body)
    write_json(case_dir / "action.request.json", {"method": args.method, "path": args.path, "body": args.body})
    write_json(case_dir / "action.response.json", action)
    time.sleep(max(0.0, args.wait))
    edge_status: dict[str, Any] | None = None
    if args.path == "/api/dev/a_edge_capture":
        # The bridge retains the exact before-edge/after-frame samples after
        # completion. Persist them beside the semantic before/after reads so
        # a UI probe can be reanalysed without sending another input edge.
        edge_response = api.get("/api/dev/a_edge_capture")
        edge_status = payload(edge_response)
        write_json(case_dir / "a_edge_capture.status.json", edge_response)
    after = collect(api, case_dir / "after", "after", ranges)

    before_id = _battle_identity(before)
    after_id = _battle_identity(after)
    result = {
        "before": before_id,
        "after": after_id,
        "battle_identity_changed": before_id != after_id,
        "memory": memory_diff(before.get("memory"), after.get("memory")),
        "a_edge_capture": edge_status,
        "screen_sha256": {"before": None, "after": None},
    }
    if args.capture_always:
        after_capture = capture(api, case_dir / "after", f"after_{args.case_id}")
        result["screen_sha256"] = {
            "before": (before_capture or {}).get("sha256"),
            "after": after_capture.get("sha256"),
        }
    write_json(case_dir / "diff.json", result)
    print(json.dumps({
        "case_id": args.case_id,
        "action_status": action.get("status_code"),
        "before_active": before_id.get("active"),
        "after_active": after_id.get("active"),
        "battle_identity_changed": result["battle_identity_changed"],
        "memory_changed_ranges": [
            name for name, item in result["memory"]["ranges"].items()
            if item.get("changed_count", 0) > 0
        ],
    }, ensure_ascii=False, indent=2))
    return 0 if action.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", default="runtime/evidence/battle_probe")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--objective", default="one controlled battle action")
    parser.add_argument("--method", default="POST")
    parser.add_argument("--path", required=True)
    parser.add_argument("--body", type=json.loads, default=None)
    parser.add_argument("--ranges")
    parser.add_argument("--wait", type=float, default=0.8)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--capture-always", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
