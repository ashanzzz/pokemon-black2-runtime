"""Savestate result classification shared by the bridge and HTTP API.

BizHawk's Lua ``savestate.loadslot`` API returns a boolean.  The old bridge
discarded that value and therefore turned a rejected load into a false
success.  Keep the classification here so every caller gets the same
recoverable, user-facing error contract.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping


INCOMPATIBLE_CODE = "SAVESTATE_INCOMPATIBLE"
UNCONFIRMED_CODE = "SAVESTATE_UNCONFIRMED"
LOAD_FAILED_CODE = "SAVESTATE_LOAD_FAILED"
SAVE_FAILED_CODE = "SAVESTATE_SAVE_FAILED"
BRIDGE_UNAVAILABLE_CODE = "BRIDGE_UNAVAILABLE"

_INCOMPATIBLE_MARKERS = (
    "different core",
    "different sync settings",
    "loadstate cancelled",
    "load state cancelled",
    "loadstate canceled",
    "load state canceled",
    "incompatible",
    "savestate rejected",
    "state rejected",
)
_MISSING_MARKERS = (
    "not found",
    "does not exist",
    "no savestate",
    "no save state",
    "missing savestate",
)


def _result_text(result: Mapping[str, Any] | None) -> str:
    if not result:
        return ""
    values = [result.get(key) for key in ("error", "message", "reason", "status", "error_kind")]
    return " ".join(str(value) for value in values if value not in (None, ""))


def is_confirmed_load(result: Mapping[str, Any] | None) -> bool:
    """Return true only for the new bridge's explicit success contract."""

    if not result:
        return False
    return (
        result.get("status") == "loaded"
        and result.get("loaded") is True
        and result.get("confirmed") is True
    )


def classify_load_failure(
    result: Mapping[str, Any] | None = None,
    error: str | None = None,
    *,
    slot: int | None = None,
) -> Dict[str, Any]:
    """Build a stable API error payload without claiming a load occurred."""

    result = result or {}
    raw = " ".join(part for part in (str(error or ""), _result_text(result)) if part).strip()
    lowered = raw.casefold()
    status = str(result.get("status") or "").casefold()

    if status in {"incompatible", "rejected"} or any(marker in lowered for marker in _INCOMPATIBLE_MARKERS):
        code = INCOMPATIBLE_CODE
        message = "模拟器拒绝加载该即时存档：它可能来自不同的核心或同步设置。"
        recovery = "当前游戏保持不变；请使用当前 BizHawk 核心和同步设置重新保存后再试。"
        http_status = 409
    elif status in {"missing", "not_found"} or any(marker in lowered for marker in _MISSING_MARKERS):
        code = LOAD_FAILED_CODE
        message = "即时存档槽位不存在或当前不可读。"
        recovery = "当前游戏保持不变；请选择已有槽位，或先在当前运行环境保存一个新槽位。"
        http_status = 404
    elif status in {"unconfirmed", "unknown"} or not raw:
        code = UNCONFIRMED_CODE
        message = "模拟器没有确认即时存档加载结果。"
        recovery = "当前游戏保持不变；请重新运行最新的 black2_bridge.lua 后再试。"
        http_status = 503
    else:
        code = LOAD_FAILED_CODE
        message = "即时存档加载失败，当前游戏未切换。"
        recovery = "当前游戏保持不变；请检查 BizHawk 核心、同步设置和存档槽位后再试。"
        http_status = 502

    return {
        "code": code,
        "message": message,
        "recovery": recovery,
        "slot": slot if slot is not None else result.get("slot"),
        "preserved_current_game": True,
        "retryable": False if code == INCOMPATIBLE_CODE else True,
        "raw_error": raw or None,
        "bridge_result": dict(result),
        "http_status": http_status,
    }


def classify_save_failure(result: Mapping[str, Any] | None = None, *, slot: int | None = None) -> Dict[str, Any]:
    """Build a stable API error payload for an unconfirmed save."""

    result = result or {}
    return {
        "code": SAVE_FAILED_CODE,
        "message": "模拟器没有确认即时存档保存成功。",
        "recovery": "当前游戏继续运行；请检查当前核心和存档目录后再试。",
        "slot": slot if slot is not None else result.get("slot"),
        "preserved_current_game": True,
        "retryable": True,
        "raw_error": _result_text(result) or None,
        "bridge_result": dict(result),
        "http_status": 502,
    }


def classify_bridge_unavailable(*, slot: int | None = None) -> Dict[str, Any]:
    """Return a structured, non-mutating response when no Lua bridge is live."""

    return {
        "code": BRIDGE_UNAVAILABLE_CODE,
        "message": "BizHawk Bridge 当前未连接，未执行即时存档操作。",
        "recovery": "当前游戏不受影响；请在 BizHawk 重新运行最新的 black2_bridge.lua 后再试。",
        "slot": slot,
        "preserved_current_game": True,
        "retryable": True,
        "raw_error": None,
        "http_status": 503,
    }
