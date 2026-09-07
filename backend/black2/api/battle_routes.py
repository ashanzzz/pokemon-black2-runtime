"""Evidence-gated battle observation and action contracts.

The public API is intentionally useful before the RAM decoder exists: clients
can validate action payloads and inspect the evidence needed for execution,
while every runtime fact remains explicitly unresolved.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints


router = APIRouter(prefix="/api/v1/battle", tags=["battle-v1"])

MoveTarget = Annotated[
    str,
    StringConstraints(pattern=r"^(player|opponent):[0-2]$"),
]
ItemTarget = Annotated[
    str,
    StringConstraints(pattern=r"^((player|opponent):[0-2]|party:[1-6])$"),
]


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


EVIDENCE_REQUIREMENTS = [
    "A battle-active locator verified against paired overworld and battle RAM captures.",
    "Battle kind, phase, menu, cursor, and legal-action decoding verified across frames.",
    "Party, move-slot, item-id, and target mappings verified against visible game state.",
    "Closed-loop input execution with post-action state and failure verification.",
]

ACTION_CONTRACTS = {
    "use_move": {
        "executable": False,
        "fields": {
            "type": {"const": "use_move"},
            "slot": {"type": "integer", "minimum": 1, "maximum": 4},
            "target": {
                "type": ["string", "null"],
                "pattern": "^(player|opponent):[0-2]$",
                "required": False,
            },
        },
    },
    "switch": {
        "executable": False,
        "fields": {
            "type": {"const": "switch"},
            "party_slot": {"type": "integer", "minimum": 1, "maximum": 6},
        },
    },
    "use_item": {
        "executable": False,
        "fields": {
            "type": {"const": "use_item"},
            "item_id": {"type": "integer", "minimum": 1, "maximum": 65535},
            "target": {
                "type": ["string", "null"],
                "pattern": "^((player|opponent):[0-2]|party:[1-6])$",
                "required": False,
            },
        },
    },
    "run": {
        "executable": False,
        "fields": {"type": {"const": "run"}},
    },
}


def _unresolved_reason() -> dict:
    return {
        "code": "BATTLE_RUNTIME_UNRESOLVED",
        "message": (
            "No verified Pokemon Black 2 battle RAM decoder is available; "
            "battle activity and legal actions cannot be determined safely."
        ),
    }


@router.get("/capabilities")
async def battle_capabilities() -> dict:
    return {
        "format": "black2-battle-capabilities/v1",
        "decoder": {
            "status": "RESEARCH",
            "verified": False,
            "confidence": 0.0,
            "can_detect": [],
        },
        "read": {
            "state": "/api/v1/battle/state",
            "actions": "/api/v1/battle/actions",
        },
        "write": {"actions": "/api/v1/battle/actions"},
        "action_discriminator": "type",
        "action_contracts": ACTION_CONTRACTS,
        "execution": {
            "available": False,
            "requires_verified_current_state": True,
            "blind_menu_input_allowed": False,
        },
        "reason": _unresolved_reason(),
        "evidence_requirements": EVIDENCE_REQUIREMENTS,
    }


@router.get("/state")
async def battle_state() -> dict:
    return {
        "format": "black2-battle-state/v1",
        "status": "unresolved",
        "active": None,
        "active_status": "unresolved",
        "battle_type": "unresolved",
        "phase": "unresolved",
        "turn": None,
        "menu": {
            "status": "unresolved",
            "kind": None,
            "cursor": None,
            "legal_targets": None,
        },
        "player_side": {"status": "unresolved", "active": None, "party": None},
        "opponent_side": {"status": "unresolved", "active": None, "party": None},
        "available_actions": [],
        "execution_available": False,
        "reason": _unresolved_reason(),
        "evidence": {
            "verified": False,
            "confidence": 0.0,
            "source": "no_verified_runtime_decoder",
            "frame": None,
            "evidence_requirements": EVIDENCE_REQUIREMENTS,
        },
    }


@router.get("/actions")
async def battle_actions() -> dict:
    return {
        "format": "black2-battle-actions/v1",
        "status": "unresolved",
        "execution_available": False,
        "available_actions": [],
        "action_contracts": ACTION_CONTRACTS,
        "reason": _unresolved_reason(),
        "evidence_requirements": EVIDENCE_REQUIREMENTS,
    }


@router.post(
    "/actions",
    status_code=409,
    responses={
        409: {"description": "Battle runtime evidence is unresolved; action rejected."},
        422: {"description": "Action payload does not match the discriminated union."},
    },
)
async def execute_battle_action(action: BattleAction, request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "format": "black2-battle-action-result/v1",
            "status": "rejected",
            "executed": False,
            "action": action.model_dump(mode="json", exclude_none=True),
            "reason": _unresolved_reason(),
            "evidence_requirements": EVIDENCE_REQUIREMENTS,
            "request_id": request.headers.get("x-request-id"),
        },
    )
