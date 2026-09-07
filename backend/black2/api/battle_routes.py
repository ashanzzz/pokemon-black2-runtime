"""Evidence-gated battle observation and semantic decision contracts.

The runtime now exposes a conservative IREJ rev.1 battle-presence candidate
from a recovered GameData -> FieldStatus chain.  This is intentionally *not*
an action executor.  Legal move/item/target/menu decoding and post-action
verification are still required before any battle command can mutate BizHawk.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..decoders.battle_runtime import BattleRuntimeDecoder
from ..memory.reader import MemoryReader
from ..runtime.hub import RuntimeHub

router = APIRouter(prefix="/api/v1/battle", tags=["battle-v2"])
_reader: MemoryReader | None = None
_hub: RuntimeHub | None = None
_decoder = BattleRuntimeDecoder()

MoveTarget = Annotated[str, StringConstraints(pattern=r"^(player|opponent):[0-2]$")]
ItemTarget = Annotated[str, StringConstraints(pattern=r"^((player|opponent):[0-2]|party:[1-6])$")]
Actor = Annotated[str, StringConstraints(pattern=r"^player:[0-2]$")]


def configure_battle_routes(reader: MemoryReader, hub: RuntimeHub) -> None:
    global _reader, _hub
    _reader = reader
    _hub = hub
    _decoder.configure(reader)


def _context() -> dict[str, Any]:
    if _hub is None:
        return {}
    snap = _hub.snapshot()
    semantic = snap.get("semantic") if isinstance(snap.get("semantic"), dict) else {}
    return semantic.get("context") if isinstance(semantic.get("context"), dict) else {}


# ---- legacy /actions payloads ------------------------------------------------
class UseMoveAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["use_move"]
    slot: int = Field(ge=1, le=4)
    target: MoveTarget | None = None


class SwitchAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["switch"]
    party_slot: int = Field(ge=1, le=6)


class UseItemAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["use_item"]
    item_id: int = Field(ge=1, le=65535)
    target: ItemTarget | None = None


class RunAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["run"]


BattleAction = Annotated[
    UseMoveAction | SwitchAction | UseItemAction | RunAction,
    Field(discriminator="type"),
]


# ---- v2 /decisions semantic commands ---------------------------------------
class UseMoveCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["use_move"]
    actor: Actor = "player:0"
    move_slot: int = Field(ge=1, le=4)
    target: MoveTarget | None = None


class SwitchCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["switch"]
    actor: Actor = "player:0"
    party_slot: int = Field(ge=1, le=6)


class UseItemCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["use_item"]
    actor: Actor = "player:0"
    item_id: int = Field(ge=1, le=65535)
    target: ItemTarget | None = None


class ThrowBallCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["throw_ball"]
    actor: Actor = "player:0"
    item_id: int = Field(ge=1, le=65535)
    target: MoveTarget = "opponent:0"


class RunCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["run"]
    actor: Actor = "player:0"


class ShiftCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["shift"]
    actor: Actor
    to_position: Literal["left", "center", "right"]


class RotateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["rotate"]
    actor: Actor = "player:0"
    direction: Literal["left", "right"]


BattleCommand = Annotated[
    UseMoveCommand | SwitchCommand | UseItemCommand | ThrowBallCommand | RunCommand | ShiftCommand | RotateCommand,
    Field(discriminator="type"),
]


class BattleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    battle_id: str | None = None
    request_id: str | int | None = None
    commands: list[BattleCommand] = Field(min_length=1, max_length=3)


EVIDENCE_REQUIREMENTS = [
    "Paired battle and non-battle IREJ rev.1 captures verifying the recovered FieldStatus locator.",
    "Battle kind, format, phase, command menu, cursor, and legal-action decoding verified across frames.",
    "Party, move-slot, item-id, target and capture/run legality mappings verified against visible game state.",
    "Closed-loop BizHawk input execution with request freshness and post-action state/failure verification.",
]

LEGACY_ACTION_CONTRACTS = {
    "use_move": {"executable": False, "fields": {"slot": "1..4", "target": "optional player/opponent slot"}},
    "switch": {"executable": False, "fields": {"party_slot": "1..6"}},
    "use_item": {"executable": False, "fields": {"item_id": "1..65535", "target": "optional battle/party target"}},
    "run": {"executable": False, "fields": {}},
}

DECISION_CONTRACTS = {
    "use_move": {"executable": False, "multi_actor": True, "fields": {"actor": "player:0..2", "move_slot": "1..4", "target": "optional battle slot"}},
    "switch": {"executable": False, "multi_actor": True, "fields": {"actor": "player:0..2", "party_slot": "1..6"}},
    "use_item": {"executable": False, "multi_actor": True, "fields": {"actor": "player:0..2", "item_id": "1..65535", "target": "optional battle/party target"}},
    "throw_ball": {"executable": False, "multi_actor": True, "fields": {"actor": "player:0..2", "item_id": "ball item id", "target": "opponent:0..2"}},
    "run": {"executable": False, "multi_actor": True, "fields": {"actor": "player:0..2"}},
    "shift": {"executable": False, "formats": ["triple"], "fields": {"actor": "player:0..2", "to_position": "left|center|right"}},
    "rotate": {"executable": False, "formats": ["rotation"], "fields": {"actor": "player:0..2", "direction": "left|right"}},
}


def _execution_reason(evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    if not evidence or evidence.get("active") is not True:
        return {
            "code": "BATTLE_RUNTIME_UNRESOLVED",
            "message": "A current battle cannot yet be established strongly enough for semantic action execution.",
        }
    return {
        "code": "BATTLE_ACTION_EXECUTION_UNVERIFIED",
        "message": (
            "Battle presence is observed as a candidate, but legal command/menu/target decoding and closed-loop "
            "post-action verification are not yet verified. Blind menu input is rejected."
        ),
    }


async def _evidence() -> dict[str, Any]:
    return await _decoder.sample()


def _dialogue_overlay() -> dict[str, Any]:
    context = _context()
    active = context.get("is_dialogue_active")
    return {
        "active": active if isinstance(active, bool) else None,
        "text": context.get("dialogue_text"),
        "speaker": context.get("speaker"),
        "choices": context.get("choices"),
        "recommended_action": context.get("recommended_action"),
    }


@router.get("/capabilities")
async def battle_capabilities() -> dict[str, Any]:
    evidence = await _evidence()
    return {
        "format": "black2-battle-capabilities/v1",
        "api_generation": 2,
        "decoder": {
            "status": "CANDIDATE" if evidence.get("status") == "candidate" else "RESEARCH",
            "verified": False,
            "confidence": evidence.get("confidence", 0.0),
            "can_detect": (["battle_presence_candidate", "field_busy_flag", "party_header_candidate"]
                           if _reader is not None else []),
        },
        "read": {
            "state": "/api/v1/battle/state",
            "request": "/api/v1/battle/request",
            "field": "/api/v1/battle/field",
            "party": "/api/v1/battle/party",
            "moves": "/api/v1/battle/moves",
            "items": "/api/v1/battle/items",
            "events": "/api/v1/battle/events",
            "evidence": "/api/v1/battle/evidence",
            "legacy_actions": "/api/v1/battle/actions",
        },
        "write": {"decisions": "/api/v1/battle/decisions", "legacy_actions": "/api/v1/battle/actions"},
        "decision_discriminator": "type",
        "decision_contracts": DECISION_CONTRACTS,
        "legacy_action_contracts": LEGACY_ACTION_CONTRACTS,
        "action_contracts": LEGACY_ACTION_CONTRACTS,
        "execution": {
            "available": False,
            "requires_verified_current_state": True,
            "blind_menu_input_allowed": False,
        },
        "decision_policy": {"requires_request_id_before_execution": True, "max_commands": 3},
        "reason": _execution_reason(evidence),
        "evidence_requirements": EVIDENCE_REQUIREMENTS,
    }


@router.get("/state")
async def battle_state() -> dict[str, Any]:
    evidence = await _evidence()
    dialogue = _dialogue_overlay()
    active = evidence.get("active")
    phase = "message" if active is True and dialogue.get("active") is True else "unresolved"
    return {
        "format": "black2-battle-state/v2",
        "status": "partial" if active is not None else "unresolved",
        "active": active,
        "active_status": evidence.get("active_status", "unresolved"),
        "battle_type": "unresolved",
        "classification": {
            "opponent_kind": "unresolved",
            "context": "unresolved",
            "format": "unresolved",
        },
        "phase": phase,
        "turn": None,
        "menu": {"status": "unresolved", "kind": None, "cursor": None, "legal_targets": None},
        "overlays": {"dialogue": dialogue},
        "field": {
            "status": "candidate" if active is not None else "unresolved",
            "player_active": None,
            "opponent_active": None,
            "weather": None,
            "terrain": None,
        },
        "player_side": {"status": evidence.get("party_header", {}).get("status", "unresolved"), "active": None, "party": evidence.get("party_header")},
        "opponent_side": {"status": "unresolved", "active": None, "party": None},
        "available_actions": [],
        "execution_available": False,
        "reason": _execution_reason(evidence),
        "evidence": evidence,
    }


@router.get("/request")
async def battle_request() -> dict[str, Any]:
    evidence = await _evidence()
    dialogue = _dialogue_overlay()
    active = evidence.get("active")
    blocked_by_dialogue = active is True and dialogue.get("active") is True
    return {
        "format": "black2-battle-request/v1",
        "status": "partial" if active is True else "unresolved",
        "battle_id": None,
        "request_id": None,
        "active": active,
        "phase": "message" if blocked_by_dialogue else "unresolved",
        "waiting_for_player": None,
        "blocking_overlay": "dialogue" if blocked_by_dialogue else None,
        "actors": [{
            "actor": "player:0",
            "pokemon": None,
            "legal_actions": [],
            "legal_actions_known": False,
        }] if active is True else [],
        "decision_contracts": DECISION_CONTRACTS,
        "execution_available": False,
        "reason": _execution_reason(evidence),
        "evidence": evidence,
    }


@router.get("/field")
async def battle_field() -> dict[str, Any]:
    state = await battle_state()
    return {
        "format": "black2-battle-field/v1",
        "status": state["status"],
        "active": state["active"],
        "classification": state["classification"],
        "field": state["field"],
        "player_side": state["player_side"],
        "opponent_side": state["opponent_side"],
        "overlays": state["overlays"],
        "contents_known": False,
        "reason": "Battle presence can be sampled, but battle-mon structures have not been verified for IREJ rev.1.",
    }


@router.get("/party")
async def battle_party() -> dict[str, Any]:
    evidence = await _evidence()
    header = evidence.get("party_header") or {}
    return {
        "format": "black2-battle-party/v1",
        "status": "partial" if header.get("status") == "candidate" else "unresolved",
        "count": header.get("count"),
        "capacity": header.get("capacity"),
        "slots": [],
        "contents_known": False,
        "available_fields_when_verified": ["species", "nickname", "level", "hp", "max_hp", "status", "held_item", "moves", "pp"],
        "reason": "PokeParty header is structurally plausible; encrypted/shuffled party slot bodies are not exposed as facts yet.",
        "evidence": evidence,
    }


@router.get("/moves")
async def battle_moves(actor: str = Query("player:0", pattern=r"^player:[0-2]$")) -> dict[str, Any]:
    evidence = await _evidence()
    return {
        "format": "black2-battle-moves/v1",
        "status": "unresolved",
        "actor": actor,
        "moves": [],
        "contents_known": False,
        "dex_contract": "/api/v1/dex/moves/{id}",
        "runtime_fields_when_verified": ["slot", "move_id", "pp_current", "pp_max", "usable", "disabled_reason", "legal_targets"],
        "reason": "Live move-slot and PP decryption/validation is not yet verified for the IREJ battle runtime.",
        "evidence": evidence,
    }


@router.get("/items")
async def battle_items(category: Literal["all", "recovery", "status_restore", "pokeballs", "battle_items"] = "all") -> dict[str, Any]:
    evidence = await _evidence()
    return {
        "format": "black2-battle-items/v1",
        "status": "unresolved",
        "category": category,
        "categories": ["recovery", "status_restore", "pokeballs", "battle_items"],
        "items": [],
        "contents_known": False,
        "capture_allowed": None,
        "reason": "Bag pointer layout is known structurally, but live battle-usable item filtering and capture legality are not verified.",
        "evidence": evidence,
    }


@router.get("/events")
async def battle_events(since: int | None = Query(None, ge=0)) -> dict[str, Any]:
    evidence = await _evidence()
    return {
        "format": "black2-battle-events/v1",
        "status": "unresolved",
        "since": since,
        "next_event_id": None,
        "events": [],
        "contents_known": False,
        "reason": "No verified IREJ battle event stream exists yet; an empty list means unknown, not no events.",
        "evidence": evidence,
    }


@router.get("/evidence")
async def battle_evidence() -> dict[str, Any]:
    return await _evidence()


@router.get("/actions")
async def battle_actions() -> dict[str, Any]:
    evidence = await _evidence()
    return {
        "format": "black2-battle-actions/v1",
        "status": "partial" if evidence.get("active") is True else "unresolved",
        "execution_available": False,
        "available_actions": [],
        "action_contracts": LEGACY_ACTION_CONTRACTS,
        "reason": _execution_reason(evidence),
        "evidence_requirements": EVIDENCE_REQUIREMENTS,
        "migration": {"preferred_read": "/api/v1/battle/request", "preferred_write": "/api/v1/battle/decisions"},
    }


@router.post("/actions", status_code=409)
async def execute_battle_action(action: BattleAction, request: Request) -> JSONResponse:
    evidence = await _evidence()
    return JSONResponse(
        status_code=409,
        content={
            "format": "black2-battle-action-result/v1",
            "status": "rejected",
            "executed": False,
            "action": action.model_dump(mode="json", exclude_none=True),
            "reason": _execution_reason(evidence),
            "evidence_requirements": EVIDENCE_REQUIREMENTS,
            "request_id": request.headers.get("x-request-id"),
        },
    )


@router.post("/decisions", status_code=409)
async def execute_battle_decision(decision: BattleDecision, request: Request) -> JSONResponse:
    evidence = await _evidence()
    return JSONResponse(
        status_code=409,
        content={
            "format": "black2-battle-decision-result/v1",
            "status": "rejected",
            "executed": False,
            "decision": decision.model_dump(mode="json", exclude_none=True),
            "reason": _execution_reason(evidence),
            "evidence_requirements": EVIDENCE_REQUIREMENTS,
            "transport_request_id": request.headers.get("x-request-id"),
        },
    )
