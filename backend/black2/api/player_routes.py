"""Player-runtime endpoints: structure-grounded movement/orientation inspection."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..memory.reader import MemoryReader
from ..world.runtime_player_state import player_runtime_service


router = APIRouter(prefix="/api/v1/player", tags=["player-runtime"])
_reader: MemoryReader | None = None


def configure_player_routes(reader: MemoryReader) -> None:
    global _reader
    _reader = reader


def _player_reader() -> MemoryReader:
    if _reader is None:
        raise RuntimeError("player routes are not configured")
    return _reader


class GaitCalibrationRequest(BaseModel):
    label: str


@router.get("/runtime")
async def runtime_player(reader: MemoryReader = Depends(_player_reader)) -> dict[str, Any]:
    try:
        return await player_runtime_service.sample(reader)
    except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/calibration")
async def gait_calibration() -> dict[str, Any]:
    return player_runtime_service.calibration_profile()


@router.post("/calibration")
async def record_gait_calibration(
    request: GaitCalibrationRequest,
    reader: MemoryReader = Depends(_player_reader),
) -> dict[str, Any]:
    # Refresh immediately so the labelled sample is as close as possible to the
    # operator-observed walk/run state.
    await player_runtime_service.sample(reader)
    result = player_runtime_service.record_gait_sample(request.label)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return result


@router.delete("/calibration")
async def clear_gait_calibration() -> dict[str, Any]:
    return {"ok": True, "profile": player_runtime_service.reset_calibration()}
