"""Read-only progression and story-evidence API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..memory.reader import MemoryReader
from ..runtime.hub import RuntimeHub
from ..world.runtime_player_state import player_runtime_service
from ..progression.state import progression_state_service

router = APIRouter(prefix="/api/v1/progression", tags=["progression-v1"])


def configure_progression_routes(reader: MemoryReader, hub: RuntimeHub | None = None) -> None:
    progression_state_service.configure(reader, player_runtime_service)
    from ..decoders.completion_decoder import completion_decoder
    completion_decoder.configure(reader)


@router.get("/state")
async def progression_state() -> dict[str, Any]:
    return await progression_state_service.sample()


@router.get("/flags")
async def progression_flags() -> dict[str, Any]:
    """Expose the current live EventWork bitfield without semantic guessing."""
    state = await progression_state_service.sample()
    story = state.get("story_flags", {}) if isinstance(state.get("story_flags"), dict) else {}
    event_work = state.get("event_work", {}) if isinstance(state.get("event_work"), dict) else {}
    flag_bytes = event_work.get("flag_bytes", {}) if isinstance(event_work.get("flag_bytes"), dict) else {}
    raw_verified = bool(flag_bytes.get("raw_hex")) and story.get("status") == "raw_verified"
    set_ids = flag_bytes.get("set_flag_ids") if isinstance(flag_bytes.get("set_flag_ids"), list) else []
    return {
        "format": "black2-progression-flags/v2",
        "status": "raw_verified" if raw_verified else state.get("status", "unresolved"),
        "confidence": "verified_raw" if raw_verified else state.get("confidence", "unresolved"),
        "contents_known": raw_verified,
        "semantic_labels_known": False,
        "flags": [{"id": int(flag_id), "is_set": True, "source": "live EventWork bitfield"} for flag_id in set_ids],
        "set_flag_ids": [int(flag_id) for flag_id in set_ids],
        "raw_evidence": event_work,
        "evidence": {
            **(state.get("evidence", {}) if isinstance(state.get("evidence"), dict) else {}),
            "source": "GameData -> EventWorkSave live Main RAM",
            "verified_raw_bitfield": raw_verified,
            "semantic_status": "raw_bit_ids_only",
        },
    }


@router.get("/gates")
async def progression_gates() -> dict[str, Any]:
    state = await progression_state_service.sample()
    gates = state.get("gates", [])
    known = bool(gates and state.get("contents_known"))
    unlocked = [g for g in gates if g.get("unlocked")]
    locked = [g for g in gates if not g.get("unlocked")]
    active_blocker = locked[0] if locked else None
    return {
        "format": "black2-progression-gates/v1",
        "status": "verified" if known else state.get("status", "unresolved"),
        "confidence": "verified" if known else state.get("confidence", "unresolved"),
        "contents_known": known,
        "count": len(gates),
        "unlocked_count": len(unlocked),
        "locked_count": len(locked),
        "active_blocking_gate": active_blocker,
        "gates": gates,
        "evidence": state.get("evidence", {"verified": False}),
    }



@router.get("/completion")
async def progression_completion() -> dict[str, Any]:
    from ..decoders.completion_decoder import completion_decoder
    return await completion_decoder.sample()
