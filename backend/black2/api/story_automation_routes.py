"""High-level evidence-gated story automation endpoints.

Navigation and encounter remain separate public APIs.  This router is the
small orchestration layer for a *story interaction*: resolve a verified NPC or
service, use the existing closed-loop navigation task, then advance dialogue
until it ends or a choice requires an explicit policy.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..world.story_automation import StoryAutomationError, StoryAutomationService


router = APIRouter(prefix="/api/v1/agent", tags=["story-automation-v1"])
_service: StoryAutomationService | None = None


def configure_story_automation_routes(service: StoryAutomationService) -> None:
    global _service
    _service = service


def _get_service() -> StoryAutomationService:
    if _service is None:
        raise StoryAutomationError(
            "AUTOMATION_UNAVAILABLE",
            "High-level story automation is not configured.",
            status_code=503,
        )
    return _service


def _error(exc: StoryAutomationError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


class AutomationBaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    movement_mode: Literal["auto", "walk", "run", "bike", "surf"] = "auto"
    max_steps: int = Field(default=2000, ge=1, le=10000)
    auto_dialogue: bool = True
    max_dialogue_steps: int = Field(default=64, ge=1, le=256)
    choice_policy: Literal["stop"] = "stop"
    correlation_id: str | None = None


class InteractRequest(AutomationBaseRequest):
    target: dict[str, Any] = Field(default_factory=dict)


class RecoveryRequest(AutomationBaseRequest):
    service: Literal["nearest", "recovery", "pokemon_center", "heal"] = "nearest"


@router.get("/story/plan")
async def story_progression_plan() -> Any:
    """Return the macro-level autonomous story plan towards the active milestone."""
    from ..world.story_runner import story_runner
    return await story_runner.plan_next_story_action()


@router.post("/story/step")
async def story_progression_step() -> Any:
    """Execute one autonomous step towards the active story milestone."""
    from ..world.story_runner import story_runner
    return await story_runner.execute_story_step()


@router.get("/automation/capabilities")
async def automation_capabilities() -> Any:
    try:
        return _get_service().capabilities()
    except StoryAutomationError as exc:
        return _error(exc)


@router.get("/services/nearby")
async def nearby_services(
    service_type: str = Query("recovery", min_length=1, max_length=64),
    zone_id: int | None = Query(None, ge=0),
) -> Any:
    try:
        return _get_service().nearby_services(service_type=service_type, zone_id=zone_id)
    except StoryAutomationError as exc:
        return _error(exc)
    except (IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "AUTOMATION_SERVICE_RESOLUTION_FAILED",
                    "message": f"{type(exc).__name__}: {exc}",
                    "details": {},
                }
            },
        )


@router.post("/automation/interact", status_code=202)
async def start_interaction(body: InteractRequest, request: Request) -> Any:
    try:
        correlation_id = body.correlation_id or request.headers.get("x-request-id")
        return await _get_service().start_interact_async(
            target=body.target,
            movement_mode=body.movement_mode,
            max_steps=body.max_steps,
            auto_dialogue=body.auto_dialogue,
            max_dialogue_steps=body.max_dialogue_steps,
            choice_policy=body.choice_policy,
            correlation_id=correlation_id,
        )
    except StoryAutomationError as exc:
        return _error(exc)


@router.post("/automation/recover", status_code=202)
@router.post("/automation/recovery", status_code=202)
async def start_recovery(body: RecoveryRequest, request: Request) -> Any:
    try:
        correlation_id = body.correlation_id or request.headers.get("x-request-id")
        return _get_service().start_recovery(
            service=body.service,
            movement_mode=body.movement_mode,
            max_steps=body.max_steps,
            auto_dialogue=body.auto_dialogue,
            max_dialogue_steps=body.max_dialogue_steps,
            choice_policy=body.choice_policy,
            correlation_id=correlation_id,
        )
    except StoryAutomationError as exc:
        return _error(exc)


@router.get("/automation/tasks/{task_id}")
async def automation_task(task_id: str) -> Any:
    try:
        return _get_service().get(task_id)
    except StoryAutomationError as exc:
        return _error(exc)


@router.post("/automation/tasks/{task_id}/cancel")
async def cancel_automation_task(task_id: str) -> Any:
    try:
        return await _get_service().cancel(task_id)
    except StoryAutomationError as exc:
        return _error(exc)
