#!/usr/bin/env python3
"""Escape one verified wild battle with a bounded, logged input sequence.

This is a calibrated fallback while the battle-menu cursor field is still
unresolved.  It is intentionally narrower than a general battle executor:
the preflight must classify the active encounter as a wild-battle candidate,
the sequence is sent one key at a time, and a failed postcondition never
causes an automatic retry.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

import requests


class ApiError(RuntimeError):
    pass


def request(session: requests.Session, base: str, method: str, path: str, body: Any = None) -> dict[str, Any]:
    response = session.request(
        method,
        base.rstrip("/") + path,
        json=body,
        timeout=20,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiError(f"{method} {path} returned non-JSON HTTP {response.status_code}") from exc
    return {"status_code": response.status_code, "ok": response.ok, "payload": payload}


def payload(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("payload")
    return value if isinstance(value, dict) else {}


def sample(session: requests.Session, base: str) -> dict[str, Any]:
    current = payload(request(session, base, "GET", "/api/v1/game/current"))
    state = payload(request(session, base, "GET", "/api/v1/battle/state"))
    identity = payload(request(session, base, "GET", "/api/v1/battle/identity"))
    return {
        "current": current,
        "battle": {
            "active": state.get("active"),
            "active_status": state.get("active_status"),
            "phase": state.get("phase"),
            "menu": state.get("menu"),
        },
        "identity": {
            "status": identity.get("status"),
            "battle_kind": identity.get("battle_kind"),
            "trainer": identity.get("trainer"),
            "player": identity.get("player"),
            "opponent": identity.get("opponent"),
        },
    }


def capture_on_mismatch(session: requests.Session, base: str, label: str) -> dict[str, Any] | None:
    try:
        return payload(request(session, base, "POST", "/api/dev/capture", {"label": label}))
    except Exception as exc:  # pragma: no cover - diagnostics must not hide the primary failure
        return {"status": "capture_failed", "error": f"{type(exc).__name__}: {exc}"}


def run(args: argparse.Namespace) -> int:
    output = Path(args.log).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    record: dict[str, Any] = {
        "format": "black2-wild-battle-escape/v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "policy": {
            "precondition": "battle.active=true and battle_kind.value=wild",
            "sequence": [
                {"button": "Down", "purpose": "calibrated FIGHT-to-BAG transition"},
                {"button": "Right", "purpose": "calibrated BAG-to-RUN transition"},
                {"button": "A", "purpose": "confirm visually/RAM-calibrated RUN"},
            ],
            "postcondition": "battle.active=false",
            "cursor_ram": "unresolved; sequence is a bounded calibrated fallback",
            "retry_policy": "never retry after a mismatch",
        },
        "writes_performed": False,
        "game_input_performed": False,
        "steps": [],
    }

    before = sample(session, args.base)
    record["before"] = before
    kind = ((before.get("identity") or {}).get("battle_kind") or {})
    if before.get("battle", {}).get("active") is not True:
        record["status"] = "precondition_failed"
        record["stop_reason"] = "battle_not_active"
    elif kind.get("value") != "wild":
        record["status"] = "precondition_failed"
        record["stop_reason"] = {
            "code": "BATTLE_KIND_NOT_WILD",
            "observed": kind,
            "message": "Refusing to send the calibrated wild escape sequence when trainer/link kind is not excluded.",
        }
    else:
        for index, step in enumerate(record["policy"]["sequence"]):
            action = request(
                session,
                args.base,
                "POST",
                "/api/actions/press",
                {"button": step["button"], "frames": 1},
            )
            time.sleep(args.settle)
            after_step = sample(session, args.base)
            step_record = {
                "index": index,
                **step,
                "action": action,
                "after": after_step,
            }
            record["steps"].append(step_record)
            record["writes_performed"] = True
            record["game_input_performed"] = True
            if not action.get("ok"):
                record["status"] = "failed"
                record["stop_reason"] = {"code": "INPUT_REJECTED", "index": index}
                break
            if step["button"] != "A" and after_step.get("battle", {}).get("active") is not True:
                record["status"] = "failed"
                record["stop_reason"] = {
                    "code": "BATTLE_ENDED_BEFORE_CONFIRM",
                    "index": index,
                    "message": "A directional edge unexpectedly ended the battle; no further input was sent.",
                }
                break
            if step["button"] == "A":
                break

        if record.get("status") is None:
            deadline = time.monotonic() + args.timeout
            final = record["steps"][-1].get("after", {}) if record["steps"] else {}
            while time.monotonic() < deadline:
                final = sample(session, args.base)
                if final.get("battle", {}).get("active") is False:
                    break
                time.sleep(args.poll)
            record["after"] = final
            if final.get("battle", {}).get("active") is False:
                record["status"] = "succeeded"
            else:
                record["status"] = "unverified"
                record["stop_reason"] = {
                    "code": "BATTLE_STILL_ACTIVE",
                    "message": "Escape was not verified; no retry was issued.",
                }
                if args.capture_on_mismatch:
                    record["mismatch_capture"] = capture_on_mismatch(
                        session, args.base, "wild_escape_mismatch",
                    )

    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": record.get("status"),
        "writes_performed": record.get("writes_performed"),
        "steps": len(record.get("steps", [])),
        "log": str(output),
        "stop_reason": record.get("stop_reason"),
    }, ensure_ascii=False, indent=2))
    return 0 if record.get("status") in {"succeeded", "precondition_failed"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--settle", type=float, default=0.35)
    parser.add_argument("--poll", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--capture-on-mismatch", action="store_true")
    parser.add_argument("--log", default="runtime/evidence/wild_escape/latest.json")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
