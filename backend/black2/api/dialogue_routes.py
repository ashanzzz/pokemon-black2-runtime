"""Public REST API for dialogue inspection and automated progression."""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..actions.input_engine import ActionEngine
from ..runtime.hub import RuntimeHub
from ..state.engine import SemanticStateEngine


router = APIRouter(prefix="/api/v1/dialogue", tags=["dialogue-v1"])

_action_engine: ActionEngine | None = None
_state_engine: SemanticStateEngine | None = None
_runtime_hub: RuntimeHub | None = None


def configure_dialogue_routes(
    action_engine: ActionEngine,
    state_engine: SemanticStateEngine,
    runtime_hub: RuntimeHub,
) -> None:
    global _action_engine, _state_engine, _runtime_hub
    _action_engine = action_engine
    _state_engine = state_engine
    _runtime_hub = runtime_hub


def _get_services() -> tuple[ActionEngine, SemanticStateEngine, RuntimeHub]:
    if _action_engine is None or _state_engine is None or _runtime_hub is None:
        raise HTTPException(
            status_code=503,
            detail="Dialogue API is not configured with runtime services.",
        )
    return _action_engine, _state_engine, _runtime_hub


class DialogueAdvanceRequest(BaseModel):
    max_steps: int = Field(default=20, ge=1, le=100, description="Max button presses to advance dialogue")
    button: Literal["A", "B"] = Field(default="A", description="Button to press for advancing")
    step_delay_ms: int = Field(default=250, ge=50, le=2000, description="Delay between presses in milliseconds")
    stop_on_choice: bool = Field(default=True, description="Stop immediately if a choice menu appears")


def _clean_dialogue_text(raw_text: str | None) -> str:
    if not raw_text:
        return ""
    cleaned = raw_text.replace("[SCROLL]", "\n").replace("[CLEAR]", "\n")
    lines = [line.strip() for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line)


@router.get("/current")
async def get_current_dialogue() -> dict[str, Any]:
    """Inspect active dialogue state, text, speaker, and choice options."""
    _action, state_eng, hub = _get_services()
    try:
        sample = await state_eng.sample_once()
        state = sample.model_dump()
    except Exception:
        state = hub.snapshot().get("semantic") or {}

    screen = state.get("context", {}) if isinstance(state.get("context"), dict) else {}
    raw_text = screen.get("loaded_dialogue_text") or screen.get("dialogue_text") or screen.get("full_dialogue_text") or ""
    clean_text = _clean_dialogue_text(raw_text)

    is_active = bool(screen.get("is_dialogue_active") or screen.get("screen_type") in ("DIALOGUE_ACTIVE", "DIALOGUE_CHOICE"))
    choices = [c.get("label", str(c)) if isinstance(c, dict) else str(c) for c in (screen.get("choices") or [])]

    return {
        "format": "black2-dialogue-current/v1",
        "is_active": is_active,
        "screen_type": screen.get("screen_type", "OVERWORLD"),
        "can_move_player": screen.get("can_move_player", not is_active),
        "speaker": screen.get("speaker", "无活跃对话"),
        "speaker_category": screen.get("speaker_category", "IDLE"),
        "text": clean_text,
        "raw_loaded_text": raw_text,
        "has_choices": bool(choices),
        "choices": choices,
        "screen_description": screen.get("screen_description", ""),
        "available_actions": screen.get("available_actions", []),
    }


@router.post("/advance")
@router.post("/skip")
async def advance_or_skip_dialogue(req: DialogueAdvanceRequest = DialogueAdvanceRequest()) -> dict[str, Any]:
    """Read current dialogue text and iteratively advance until OVERWORLD state is restored."""
    action_eng, state_eng, hub = _get_services()

    # 1. Initial observation
    try:
        sample = await state_eng.sample_once()
        state = sample.model_dump()
    except Exception:
        state = hub.snapshot().get("semantic") or {}

    screen = state.get("context", {}) if isinstance(state.get("context"), dict) else {}
    initial_active = bool(screen.get("is_dialogue_active") or screen.get("screen_type") in ("DIALOGUE_ACTIVE", "DIALOGUE_CHOICE"))

    captured_chunks = []
    initial_raw = screen.get("loaded_dialogue_text") or screen.get("dialogue_text") or screen.get("full_dialogue_text") or ""
    if initial_raw:
        captured_chunks.append(initial_raw)

    if not initial_active and screen.get("can_move_player", True):
        return {
            "format": "black2-dialogue-advance-result/v1",
            "ok": True,
            "completed": True,
            "stopped_reason": "already_overworld",
            "dialogue_was_active": False,
            "steps_taken": 0,
            "captured_text": _clean_dialogue_text(initial_raw),
            "raw_loaded_text": initial_raw,
            "final_screen": {
                "screen_type": screen.get("screen_type", "OVERWORLD"),
                "can_move_player": True,
                "is_dialogue_active": False,
                "screen_description": screen.get("screen_description", "【大地图自由探索】主角可自由移动"),
            },
            "player_position": {
                "zone_id": state.get("field_runtime", {}).get("zone_id") if isinstance(state.get("field_runtime"), dict) else None,
                "grid": state.get("player_grid"),
            },
        }

    # 2. Advance loop
    step = 0
    stopped_reason = "max_steps_reached"
    latest_screen = screen

    while step < req.max_steps:
        # Check choice stop condition before pressing
        choices = latest_screen.get("choices") or []
        if req.stop_on_choice and (choices or latest_screen.get("screen_type") == "DIALOGUE_CHOICE"):
            stopped_reason = "choice_menu"
            break

        # Issue button press
        await action_eng.press_button(req.button, hold_frames=6, wait_frames=6)
        step += 1

        # Delay for game text engine transition
        await asyncio.sleep(req.step_delay_ms / 1000.0)

        # Sample state after press
        try:
            sample = await state_eng.sample_once()
            state = sample.model_dump()
        except Exception:
            state = hub.snapshot().get("semantic") or {}

        latest_screen = state.get("context", {}) if isinstance(state.get("context"), dict) else {}
        current_raw = latest_screen.get("loaded_dialogue_text") or latest_screen.get("dialogue_text") or ""
        if current_raw and current_raw not in captured_chunks:
            captured_chunks.append(current_raw)

        is_active = bool(latest_screen.get("is_dialogue_active") or latest_screen.get("screen_type") in ("DIALOGUE_ACTIVE", "DIALOGUE_CHOICE"))
        can_move = bool(latest_screen.get("can_move_player"))

        if not is_active and can_move:
            stopped_reason = "overworld_restored"
            break

    # 3. Compile full captured text
    combined_raw = "\n[PAGE]\n".join(captured_chunks) if captured_chunks else initial_raw
    clean_text = _clean_dialogue_text(combined_raw)

    field_runtime = state.get("field_runtime") if isinstance(state.get("field_runtime"), dict) else {}
    zone_id = field_runtime.get("zone_id") or state.get("zone_id")

    return {
        "format": "black2-dialogue-advance-result/v1",
        "ok": True,
        "completed": stopped_reason == "overworld_restored",
        "stopped_reason": stopped_reason,
        "dialogue_was_active": initial_active,
        "steps_taken": step,
        "captured_text": clean_text,
        "raw_loaded_text": combined_raw,
        "final_screen": {
            "screen_type": latest_screen.get("screen_type", "OVERWORLD"),
            "can_move_player": bool(latest_screen.get("can_move_player")),
            "is_dialogue_active": bool(latest_screen.get("is_dialogue_active")),
            "screen_description": latest_screen.get("screen_description", ""),
        },
        "player_position": {
            "zone_id": zone_id,
            "grid": state.get("player_grid"),
        },
    }
