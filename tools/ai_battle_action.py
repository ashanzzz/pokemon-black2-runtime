#!/usr/bin/env python3
"""Evidence-gated battle decision helper.

It reads the battle request, persistent party/moves and item evidence, adds
Dex names for decoded move/species IDs, and refuses to send a command until
the API explicitly reports ``execution_available=true``.  The current build
therefore produces a useful preflight log without blind menu input.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

import requests


def get(base: str, path: str) -> dict[str, Any]:
    response = requests.get(base.rstrip("/") + path, timeout=20)
    try:
        payload = response.json()
    except ValueError:
        payload = {"text": response.text[:2000]}
    return {"status_code": response.status_code, "ok": response.ok, "payload": payload}


def post(base: str, path: str, body: Any) -> dict[str, Any]:
    response = requests.post(base.rstrip("/") + path, json=body, timeout=20)
    try:
        payload = response.json()
    except ValueError:
        payload = {"text": response.text[:2000]}
    return {"status_code": response.status_code, "ok": response.ok, "payload": payload}


def run(args: argparse.Namespace) -> int:
    base = args.base.rstrip("/")
    request_result = get(base, "/api/v1/battle/request")
    party_result = get(base, "/api/v1/battle/party")
    moves_result = get(base, "/api/v1/battle/moves")
    items_result = get(base, "/api/v1/battle/items?category=all")
    request_body = request_result.get("payload") or {}
    party = party_result.get("payload") or {}
    moves = moves_result.get("payload") or {}
    item_view = items_result.get("payload") or {}
    dex_names = []
    for slot in moves.get("moves") or []:
        move_id = slot.get("move_id")
        if not isinstance(move_id, int) or move_id <= 0:
            continue
        dex = get(base, f"/api/v1/dex/moves/{move_id}").get("payload") or {}
        move = dex.get("move") if isinstance(dex, dict) else None
        if isinstance(move, dict):
            dex_names.append({"move_id": move_id, "identifier": move.get("identifier"), "names": move.get("names")})

    record: dict[str, Any] = {
        "format": "black2-ai-battle-action/v1",
        "preflight": {"request": request_result, "party": party_result, "moves": moves_result, "items": items_result, "move_names": dex_names},
        "command": None,
        "result": None,
    }
    if request_body.get("execution_available") is not True:
        record["result"] = {"status": "blocked", "code": "BATTLE_ACTION_EXECUTION_UNVERIFIED", "writes_performed": False}
    else:
        command: dict[str, Any] = {"type": args.type, "actor": "player:0"}
        if args.type == "throw_ball":
            if args.item_id is None:
                raise SystemExit("throw_ball requires --item-id")
            command.update({"item_id": args.item_id, "target": args.target})
        elif args.type == "use_item":
            if args.item_id is None:
                raise SystemExit("use_item requires --item-id")
            command.update({"item_id": args.item_id})
            if args.target:
                command["target"] = args.target
        elif args.type == "switch":
            if args.party_slot is None:
                raise SystemExit("switch requires --party-slot")
            command["party_slot"] = args.party_slot
        elif args.type == "use_move":
            if args.move_slot is None:
                raise SystemExit("use_move requires --move-slot")
            command["move_slot"] = args.move_slot
            if args.target:
                command["target"] = args.target
        record["command"] = {"battle_id": request_body.get("battle_id"), "commands": [command]}
        result = post(base, "/api/v1/battle/decisions", record["command"])
        record["result"] = {"submit": result, "after_request": get(base, "/api/v1/battle/request")}
        time.sleep(args.wait)
        record["result"]["after_party"] = get(base, "/api/v1/battle/party")

    output = Path(args.log).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": record["result"].get("status") if isinstance(record["result"], dict) else None, "log": str(output), "party_status": party.get("status"), "items_status": item_view.get("status"), "move_names": dex_names}, ensure_ascii=False, indent=2))
    return 0 if record["result"].get("status") == "blocked" else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--type", choices=("use_move", "use_item", "throw_ball", "switch"), required=True)
    parser.add_argument("--item-id", type=int)
    parser.add_argument("--party-slot", type=int)
    parser.add_argument("--move-slot", type=int)
    parser.add_argument("--target")
    parser.add_argument("--wait", type=float, default=0.8)
    parser.add_argument("--log", default="runtime/ai_context/battle_action.json")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

