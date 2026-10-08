"""REST long-poll/SSE surface for the Agent Event Protocol v1."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from ..runtime.events import agent_event_bus
from ..runtime.layered_status import normalize_screen_type
from ..runtime.wait_state import derive_wait_state


router = APIRouter(prefix="/api/v1/agent", tags=["agent-events"])
_hub: Any | None = None
_active_task_provider: Callable[[], dict[str, Any] | None] | None = None
_active_automation_provider: Callable[[], dict[str, Any] | None] | None = None
_auto_advance_provider: Callable[[], Any] | None = None


def configure_agent_event_routes(
    hub: Any,
    active_task_provider: Callable[[], dict[str, Any] | None] | None = None,
    active_automation_provider: Callable[[], dict[str, Any] | None] | None = None,
    auto_advance_provider: Callable[[], Any] | None = None,
) -> None:
    global _hub, _active_task_provider, _active_automation_provider, _auto_advance_provider
    _hub = hub
    _active_task_provider = active_task_provider
    _active_automation_provider = active_automation_provider
    _auto_advance_provider = auto_advance_provider


def _snapshot() -> dict[str, Any]:
    return _hub.snapshot() if _hub is not None else {}


class WaitAdvanceRequest(BaseModel):
    """Idempotency/precondition token for one automatic A edge."""

    model_config = ConfigDict(extra="forbid", strict=True)

    wait_id: str


@router.get("/events/cursor")
async def agent_event_cursor() -> dict[str, Any]:
    snap = _snapshot()
    transport = snap.get("transport") or {}
    return {
        "format": "black2-agent-cursor/v1",
        "cursor": agent_event_bus.cursor,
        "state_revision": agent_event_bus.state_revision,
        "session_id": transport.get("session_id"),
    }


def _validate_query(after: int, limit: int) -> None:
    if after < 0:
        raise ValueError("after must be non-negative")
    if not 1 <= limit <= 2048:
        raise ValueError("limit must be between 1 and 2048")


@router.get("/events")
async def agent_events(
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=2048),
) -> dict[str, Any]:
    _validate_query(after, limit)
    return agent_event_bus.read_since(after, limit)


@router.get("/events/log")
async def agent_events_log(limit: int = Query(100, ge=1, le=2048)) -> dict[str, Any]:
    """Return the bounded tail of the persistent event log.

    The cursor/long-poll/SSE endpoints remain the low-latency live contract;
    this endpoint is for restart-safe diagnostics and for another AI to read a
    compact event history without opening the workspace log directly.
    """
    return agent_event_bus.persisted_recent(limit)


@router.get("/events/wait")
async def agent_events_wait(
    after: int = Query(0, ge=0),
    timeout_ms: int = Query(25000, ge=0, le=60000),
    limit: int = Query(100, ge=1, le=2048),
) -> dict[str, Any]:
    _validate_query(after, limit)
    return await agent_event_bus.wait_since_checked(after, timeout=timeout_ms / 1000.0, limit=limit)


@router.get("/events/stream")
async def agent_events_stream(after: int = Query(0, ge=0)) -> StreamingResponse:
    if after < 0:
        raise ValueError("after must be non-negative")

    async def stream():
        cursor = after
        while True:
            payload = await agent_event_bus.wait_since_checked(cursor, timeout=25.0, limit=100)
            if payload.get("status") == "cursor_expired":
                yield f"event: cursor_expired\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            events = payload.get("events") or []
            if not events:
                yield ": keep-alive\n\n"
                continue
            for event in events:
                cursor = int(event["seq"])
                yield (
                    f"id: {cursor}\n"
                    f"event: {event.get('type', 'runtime.changed')}\n"
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                )

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/wait-state")
async def agent_wait_state() -> dict[str, Any]:
    """Return the current semantic wait boundary without starting a RAM read."""
    snap = _snapshot()
    wait = snap.get("wait_state") if isinstance(snap.get("wait_state"), dict) else derive_wait_state(snap)
    transport = snap.get("transport") if isinstance(snap.get("transport"), dict) else {}
    return {
        "format": "black2-agent-wait-state-response/v1",
        "wait_state": wait,
        "event_cursor": agent_event_bus.cursor,
        "state_revision": agent_event_bus.state_revision,
        "frame": transport.get("frame"),
        "session_id": transport.get("session_id"),
        "next": {
            "events": f"/api/v1/agent/events/wait?after={agent_event_bus.cursor}",
            "auto_advance": "/api/v1/agent/wait/advance",
        },
        "policy": {
            "auto_transition": "only call auto_advance when auto_policy=press_A_once and wait_id still matches",
            "decision": "do not call auto_advance; choose a semantic action and verify its result",
            "unresolved": "do not send input",
        },
    }


@router.post("/wait/advance")
async def agent_wait_advance(body: WaitAdvanceRequest) -> JSONResponse:
    """Advance exactly one evidence-gated automatic transition.

    The caller must echo the current ``wait_id``.  This prevents a delayed or
    duplicated event consumer from pressing A on a later dialogue page or on
    a newly arrived decision state.
    """
    snap = _snapshot()
    wait = snap.get("wait_state") if isinstance(snap.get("wait_state"), dict) else derive_wait_state(snap)
    if body.wait_id != wait.get("wait_id"):
        return JSONResponse(status_code=409, content={
            "format": "black2-agent-wait-action/v1",
            "status": "rejected",
            "executed": False,
            "reason": {
                "code": "WAIT_STATE_STALE",
                "message": "The wait boundary changed; reread /wait-state before acting.",
                "expected_wait_id": wait.get("wait_id"),
                "received_wait_id": body.wait_id,
            },
            "wait_state": wait,
        })
    if wait.get("kind") != "auto_transition" or wait.get("auto_policy") != "press_A_once":
        return JSONResponse(status_code=409, content={
            "format": "black2-agent-wait-action/v1",
            "status": "rejected",
            "executed": False,
            "reason": {
                "code": "WAIT_ACTION_NOT_AUTHORIZED",
                "message": "This boundary is not a one-A automatic transition.",
                "kind": wait.get("kind"),
                "reason": wait.get("reason"),
                "auto_policy": wait.get("auto_policy"),
            },
            "wait_state": wait,
        })
    if _auto_advance_provider is None:
        return JSONResponse(status_code=503, content={
            "format": "black2-agent-wait-action/v1",
            "status": "rejected",
            "executed": False,
            "reason": {"code": "WAIT_AUTO_ADVANCE_UNAVAILABLE", "message": "No automatic transition executor is configured."},
            "wait_state": wait,
        })

    transport = snap.get("transport") if isinstance(snap.get("transport"), dict) else {}
    common = {
        "frame": transport.get("frame"),
        "session_id": transport.get("session_id"),
        "resources": {"wait_state": "/api/v1/agent/wait-state"},
        "data": {"wait_id": body.wait_id, "reason": wait.get("reason")},
    }
    await agent_event_bus.publish(
        "runtime.wait.auto_advance.started",
        **common,
        summary="One evidence-gated automatic A edge started.",
    )
    try:
        result = _auto_advance_provider()
        if hasattr(result, "__await__"):
            result = await result
    except Exception as exc:
        await agent_event_bus.publish(
            "runtime.wait.auto_advance.failed",
            **common,
            severity="error",
            summary="Automatic wait transition failed before completion.",
            data={"wait_id": body.wait_id, "error_type": type(exc).__name__, "error": str(exc)[:300]},
        )
        return JSONResponse(status_code=502, content={
            "format": "black2-agent-wait-action/v1",
            "status": "failed",
            "executed": False,
            "wait_id": body.wait_id,
            "reason": {"code": "WAIT_AUTO_ADVANCE_FAILED", "message": str(exc)[:300]},
        })

    observed = await _hub.sample_once() if _hub is not None else _snapshot()
    next_wait = observed.get("wait_state") if isinstance(observed.get("wait_state"), dict) else derive_wait_state(observed)
    return JSONResponse(status_code=200, content={
        "format": "black2-agent-wait-action/v1",
        "status": "executed",
        "executed": True,
        "action": {"type": "dialogue.advance", "button": "A", "count": 1},
        "wait_id": body.wait_id,
        "result": result,
        "next_wait_state": next_wait,
        "event_cursor": agent_event_bus.cursor,
    })


@router.get("/state")
async def agent_state() -> dict[str, Any]:
    snap = _snapshot()
    semantic = snap.get("semantic") or {}
    context = semantic.get("context") or {}
    battle = snap.get("battle") or {}
    wait_state = snap.get("wait_state") if isinstance(snap.get("wait_state"), dict) else derive_wait_state(snap)
    primary = normalize_screen_type(context.get("screen_type"))
    attention: list[dict[str, Any]] = []
    if battle.get("active") is True:
        attention.append({"kind": "battle", "status": "detected", "resource": "/api/v1/battle/state"})
    if snap.get("dialogue", {}).get("active"):
        attention.append({"kind": "dialogue", "status": "active", "resource": "/api/state"})
    active_task = _active_task_provider() if _active_task_provider is not None else None
    active_automation = _active_automation_provider() if _active_automation_provider is not None else None
    transport = snap.get("transport") or {}
    return {
        "format": "black2-agent-state/v1",
        "event_cursor": agent_event_bus.cursor,
        "state_revision": agent_event_bus.state_revision,
        "frame": transport.get("frame"),
        "session_id": transport.get("session_id"),
        "primary_mode": primary,
        "active_task": active_task,
        "active_automation": active_automation,
        "attention": attention,
        "wait_state": wait_state,
        "resources": {
            "runtime": "/api/v1/runtime/snapshot",
            "game_current": "/api/v1/game/current",
            "environment": "/api/v1/game/environment",
            "dialogue": "/api/state",
            "battle": "/api/v1/battle/state",
            "battle_request": "/api/v1/battle/request",
            "map": "/api/v1/map/truth/current",
            "view_local": "/api/v1/ai/view/current?profile=local_7x7",
            "view_global": "/api/v1/ai/view/global",
            "navigation": "/api/v1/navigation/tasks",
            "navigation_hazards": "/api/v1/navigation/hazards",
            "story_automation": "/api/v1/agent/automation/capabilities",
            "nearby_services": "/api/v1/agent/services/nearby",
            "events_log": "/api/v1/agent/events/log",
            "wait_state": "/api/v1/agent/wait-state",
            "wait_advance": "/api/v1/agent/wait/advance",
        },
    }
