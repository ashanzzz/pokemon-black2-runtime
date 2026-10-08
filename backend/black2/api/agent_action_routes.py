"""REST endpoints for precondition-gated semantic actions."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..actions.prepared_actions import PreparedActionService


router = APIRouter(prefix="/api/v1/agent", tags=["agent-actions"])
_service: PreparedActionService | None = None


def configure_prepared_action_routes(service: PreparedActionService) -> None:
    global _service
    _service = service


class PreparedActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=80)
    command: dict[str, Any]
    correlation_id: str | None = Field(default=None, max_length=200)
    preconditions: dict[str, Any] = Field(default_factory=dict)
    execute_when: dict[str, Any] = Field(default_factory=dict)
    ttl_frames: int | None = Field(default=None, ge=1, le=100000)
    ttl_seconds: float | None = Field(default=None, gt=0, le=3600)


def _configured() -> PreparedActionService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Prepared action service is not configured.")
    return _service


@router.post("/actions", status_code=202)
async def submit_prepared_action(
    body: PreparedActionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        return await _configured().submit(
            kind=body.kind,
            command=body.command,
            preconditions=body.preconditions,
            execute_when=body.execute_when,
            correlation_id=body.correlation_id,
            ttl_frames=body.ttl_frames,
            ttl_seconds=body.ttl_seconds,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/actions/{action_id}")
async def prepared_action_status(action_id: str) -> dict[str, Any]:
    try:
        return _configured().get(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Prepared action was not found.") from exc


@router.post("/actions/{action_id}/cancel")
async def cancel_prepared_action(action_id: str) -> dict[str, Any]:
    try:
        return await _configured().cancel(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Prepared action was not found.") from exc
