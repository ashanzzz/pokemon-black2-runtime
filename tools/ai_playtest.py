#!/usr/bin/env python3
"""Read-only AI playtest harness for the Black 2 semantic API.

The harness is intentionally evidence-first: it polls the state, current
environment, navigation context/hazards and a 7x7 local view, then writes
newline-delimited JSON for an external Luna/Terra-style agent to inspect.  It
stops and records a blocking attention event when battle, dialogue or a map
transition appears.  No endpoint in this script sends controller input.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Api:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str) -> dict[str, Any]:
        url = self.base_url + path
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else {"value": payload}
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "url": url}


def _grid(state: dict[str, Any]) -> dict[str, Any] | None:
    exploration = state.get("exploration") if isinstance(state.get("exploration"), dict) else {}
    value = exploration.get("position")
    return value if isinstance(value, dict) else None


def _attention(snapshot: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    result = list(snapshot.get("attention") or []) if isinstance(snapshot.get("attention"), list) else []
    battle = current.get("battle") if isinstance(current.get("battle"), dict) else {}
    if battle.get("active") is True and not any(item.get("kind") == "battle" for item in result if isinstance(item, dict)):
        result.append({"kind": "battle", "status": "detected", "resource": "/api/v1/battle/state"})
    if current.get("primary_context") in {"transition", "unknown"}:
        result.append({"kind": "transition_or_unknown", "status": "attention_required"})
    return result


def sample_once(api: Api, *, include_global: bool = False) -> dict[str, Any]:
    state = api.get("/api/v1/agent/state")
    current = api.get("/api/v1/game/current")
    environment = api.get("/api/v1/game/environment")
    context = api.get("/api/v1/navigation/context")
    hazards = api.get("/api/v1/navigation/hazards")
    local_view = api.get("/api/v1/ai/view/current?" + urlencode({"profile": "local_7x7"}))
    payload: dict[str, Any] = {
        "timestamp": _now(),
        "agent_state": state,
        "game_current": current,
        "environment": environment,
        "navigation_context": context,
        "navigation_hazards": hazards,
        "local_view_7x7": local_view,
    }
    if include_global:
        payload["global_view"] = api.get("/api/v1/ai/view/global")
    payload["attention"] = _attention(state, current)
    payload["identity"] = {
        "session_id": state.get("session_id"),
        "frame": state.get("frame"),
        "primary_mode": state.get("primary_mode"),
        "zone_id": (environment.get("coordinate") or {}).get("zone_id") if isinstance(environment.get("coordinate"), dict) else None,
        "grid": _grid(current),
    }
    return payload


def run(args: argparse.Namespace) -> int:
    api = Api(args.base_url, args.timeout)
    log_path = Path(args.log).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    previous_identity: dict[str, Any] | None = None
    started = time.monotonic()
    with log_path.open("a", encoding="utf-8") as stream:
        while True:
            payload = sample_once(api, include_global=args.include_global)
            identity = payload.get("identity") or {}
            changed = previous_identity is None or identity != previous_identity
            payload["change"] = changed
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            print(json.dumps({"timestamp": payload["timestamp"], "identity": identity, "attention": payload["attention"], "changed": changed}, ensure_ascii=False))
            previous_identity = identity

            if args.stop_on_attention and payload["attention"]:
                print("Playtest paused: inspect the attention resources before sending any input.", file=sys.stderr)
                return 0
            if args.once or (args.duration > 0 and time.monotonic() - started >= args.duration):
                return 0
            time.sleep(max(0.05, args.interval))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765", help="Black 2 API base URL")
    parser.add_argument("--log", default="runtime/ai_playtest.ndjson", help="NDJSON output path")
    parser.add_argument("--interval", type=float, default=0.5, help="poll interval in seconds")
    parser.add_argument("--duration", type=float, default=0, help="stop after N seconds; 0 means until attention/interrupt")
    parser.add_argument("--timeout", type=float, default=3.0, help="per-request timeout")
    parser.add_argument("--once", action="store_true", help="take one sample and exit")
    parser.add_argument("--include-global", action="store_true", help="also fetch the global Matrix manifest")
    parser.add_argument("--no-stop-on-attention", dest="stop_on_attention", action="store_false", help="continue polling after battle/dialogue/transition")
    parser.set_defaults(stop_on_attention=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
