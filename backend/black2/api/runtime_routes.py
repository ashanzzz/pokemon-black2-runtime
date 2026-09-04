"""Stable browser-facing runtime API.

Every UI module should prefer these endpoints.  They are cache reads and never
mistake a semantic decoder failure for a disconnected HTTP/Bridge transport.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..runtime.config import runtime_config
from ..runtime.hub import RuntimeHub

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime-hub"])
_hub: RuntimeHub | None = None


def configure_runtime_routes(hub: RuntimeHub) -> None:
    global _hub
    _hub = hub


def _runtime_hub() -> RuntimeHub:
    if _hub is None:
        raise RuntimeError("runtime routes are not configured")
    return _hub


@router.get("/health")
async def health(hub: RuntimeHub = Depends(_runtime_hub)) -> dict[str, Any]:
    return hub.health()


@router.get("/snapshot")
async def snapshot(hub: RuntimeHub = Depends(_runtime_hub)) -> dict[str, Any]:
    return hub.snapshot()


@router.post("/refresh")
async def refresh(hub: RuntimeHub = Depends(_runtime_hub)) -> dict[str, Any]:
    return await hub.sample_once()


@router.get("/schema")
async def schema() -> dict[str, Any]:
    return {
        "format": "black2-runtime-ui-contract/v1",
        "ports": runtime_config.public_schema(),
        "authority": {
            "transport": "/api/v1/runtime/health",
            "aggregated_snapshot": "/api/v1/runtime/snapshot",
            "player_runtime": "/api/v1/player/runtime",
            "map_truth": "/api/v1/map/truth/current",
            "map_scene": "/api/v1/map/scene/current",
            "dialogue_history": "/api/dialogue/history",
        },
        "frontend_rule": "Never infer Backend/Bridge offline from a semantic/map/player request failure.",
    }
