"""Layered current-state endpoints for AI clients and the home dashboard."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..decoders.battle_runtime import BattleRuntimeDecoder
from ..memory.reader import MemoryReader
from ..runtime.hub import RuntimeHub
from ..runtime.layered_status import project_layered_state

router = APIRouter(prefix="/api/v1/game", tags=["game-state-v2"])
_reader: MemoryReader | None = None
_hub: RuntimeHub | None = None
_decoder = BattleRuntimeDecoder()


def configure_status_routes(reader: MemoryReader, hub: RuntimeHub) -> None:
    global _reader, _hub
    _reader = reader
    _hub = hub
    _decoder.configure(reader)


def _snapshot() -> dict[str, Any]:
    return _hub.snapshot() if _hub is not None else {
        "runtime": {"status": "unavailable", "semantic_status": "unresolved"},
        "transport": {"bridge_connected": False},
        "age_seconds": None,
    }


@router.get("/current")
async def current_game_state() -> dict[str, Any]:
    battle = await _decoder.sample()
    return project_layered_state(_snapshot(), battle)


@router.get("/layers")
async def current_game_layers() -> dict[str, Any]:
    payload = await current_game_state()
    return {
        "format": "black2-game-layers/v1",
        "status": payload["status"],
        "frame": payload["frame"],
        "primary_context": payload["primary_context"],
        "active_layers": payload["active_layers"],
        "overlays": payload["overlays"],
        "layers": payload["layers"],
        "input": payload["input"],
        "policy": "Layers are independent facts; dialogue/menu/transition may overlay exploration or battle.",
    }
