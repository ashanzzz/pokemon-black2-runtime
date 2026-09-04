"""Unified Workbench v9 browser contract.

This router is intentionally a *read-mostly aggregation layer*.  It exposes
cached runtime facts, UI capabilities, evidence indexes and lifecycle events
without performing hidden full-RAM discovery.  Heavy reverse-engineering
operations stay on their explicit existing endpoints.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ..decoders.dialogue import dialogue_timeline
from ..runtime.control_log import runtime_control_log
from ..runtime.hub import RuntimeHub
from ..runtime.versions import PROTOCOL_VERSIONS, RUNTIME_RELEASE_VERSION, component_version_report
from ..world.observed_navigation import observed_navigation_graph
from ..world.runtime_player_state import player_runtime_service
from ..world.spatial_calibration import spatial_calibration_service


router = APIRouter(prefix="/api/v1/workbench", tags=["workbench-v9"])
_hub: RuntimeHub | None = None


def configure_workbench_routes(hub: RuntimeHub) -> None:
    global _hub
    _hub = hub


def _runtime_hub() -> RuntimeHub:
    if _hub is None:
        raise RuntimeError("workbench routes are not configured")
    return _hub


def _workspaces() -> list[dict[str, Any]]:
    return [
        {"id": "world", "icon": "world", "title_key": "workspace.world", "default_editor": "world3d", "live": True},
        {"id": "player", "icon": "player", "title_key": "workspace.player", "default_editor": "player", "live": True},
        {"id": "dialogue", "icon": "dialogue", "title_key": "workspace.dialogue", "default_editor": "dialogue", "live": True},
        {"id": "memory", "icon": "memory", "title_key": "workspace.memory", "default_editor": "memory", "live": False},
        {"id": "evidence", "icon": "evidence", "title_key": "workspace.evidence", "default_editor": "evidence", "live": False},
        {"id": "monitor", "icon": "monitor", "title_key": "workspace.monitor", "default_editor": "monitor", "live": True},
        {"id": "tools", "icon": "tools", "title_key": "workspace.tools", "default_editor": "tools", "live": False},
    ]


def _tool_registry() -> list[dict[str, str]]:
    return [
        {"id": "controller", "title_key": "tool.controller", "href": "/frontend/controller.html", "risk": "write-input"},
        {"id": "ram-dumper", "title_key": "tool.ramDumper", "href": "/ram-dumper", "risk": "heavy-read"},
        {"id": "memory-tracer", "title_key": "tool.memoryTracer", "href": "/frontend/memory-tracer.html", "risk": "bounded-read"},
        {"id": "dialogue-checkpoints", "title_key": "tool.dialogueCheckpoints", "href": "/dialogue-checkpoints", "risk": "bounded-read"},
        {"id": "api-docs", "title_key": "tool.apiDocs", "href": "/docs", "risk": "none"},
    ]


@router.get("/bootstrap")
async def bootstrap(hub: RuntimeHub = Depends(_runtime_hub)) -> dict[str, Any]:
    """One cache-only payload used to mount the entire Workbench shell."""
    health = hub.health()
    snapshot = hub.snapshot()
    player = player_runtime_service.latest or {
        "status": "unresolved",
        "confidence": "unresolved",
        "reason": "PlayerRuntime has not published a cached sample yet",
    }
    return {
        "format": "black2-workbench-bootstrap/v1",
        "release": RUNTIME_RELEASE_VERSION,
        "locale": {"default": "zh-CN", "supported": ["zh-CN", "en"]},
        "health": health,
        "snapshot": snapshot,
        "player": player,
        "navigation": observed_navigation_graph.status(),
        "calibration": spatial_calibration_service.status(),
        "workspaces": _workspaces(),
        "tools": _tool_registry(),
        "confidence_levels": ["verified", "probable", "candidate", "unresolved", "error"],
        "performance_policy": {
            "bootstrap": "RuntimeHub cache only",
            "player_live": "cached PlayerRuntime",
            "scene": "event-driven; static ROM world reload only on scene identity change",
            "runtime_actors": "bounded ActorSystem + actor heap; opt-in",
            "legacy_full_ram_map_scan": "disabled by default",
        },
    }


@router.get("/events")
async def events(limit: int = 80) -> dict[str, Any]:
    """Small mixed event feed for the bottom dock; never contains RAM bytes."""
    limit = max(1, min(int(limit), 250))
    lifecycle = runtime_control_log.recent(limit)
    dialogue = dialogue_timeline.get_history(limit=min(limit, 80))
    rows: list[dict[str, Any]] = []
    for item in lifecycle:
        rows.append({
            "source": "runtime",
            "kind": item.get("operation", "runtime"),
            "time": item.get("timestamp_utc"),
            "confidence": "verified",
            "summary": item.get("result", "event"),
            "details": item.get("details") or {},
        })
    for entry in dialogue:
        data = entry.model_dump()
        rows.append({
            "source": "dialogue",
            "kind": "dialogue",
            "time": data.get("timestamp") or data.get("created_at"),
            "frame": data.get("frame"),
            "confidence": data.get("confidence") or "probable",
            "summary": data.get("text") or data.get("dialogue_text") or data.get("current_text") or "dialogue event",
            "details": data,
        })
    rows.sort(key=lambda item: (str(item.get("time") or ""), int(item.get("frame") or 0)), reverse=True)
    return {"format": "black2-workbench-events/v1", "count": len(rows[:limit]), "events": rows[:limit]}


@router.get("/evidence")
async def evidence_index() -> dict[str, Any]:
    return {
        "format": "black2-workbench-evidence/v1",
        "calibration": spatial_calibration_service.status(),
        "reports": spatial_calibration_service.list_reports(),
        "navigation": observed_navigation_graph.status(),
        "capture_endpoints": {
            "calibration_start": "/api/v1/lab/calibration/start",
            "calibration_sample": "/api/v1/lab/calibration/sample",
            "calibration_finish": "/api/v1/lab/calibration/finish",
            "universal_ram": "/ram-dumper",
            "dialogue_checkpoint": "/dialogue-checkpoints",
        },
    }


@router.get("/versions")
async def versions(hub: RuntimeHub = Depends(_runtime_hub)) -> dict[str, Any]:
    components = component_version_report(
        bridge_version=getattr(hub.transport, "bridge_version", None),
        bridge_connected=bool(hub.client.is_connected),
    )
    return {
        "format": "black2-workbench-versions/v1",
        "release": RUNTIME_RELEASE_VERSION,
        "status": "compatible" if all(row["status"] == "compatible" for row in components) else "attention",
        "components": components,
        "protocols": list(PROTOCOL_VERSIONS),
    }


@router.get("/schema")
async def schema(request: Request) -> dict[str, Any]:
    return {
        "format": "black2-workbench-ui-contract/v1",
        "release": RUNTIME_RELEASE_VERSION,
        "locale": {"default": "zh-CN", "supported": ["zh-CN", "en"], "storage_key": "black2.workbench.locale"},
        "selection_contract": {
            "common": ["kind", "id", "label", "confidence", "facts", "raw", "actions"],
            "kinds": ["scene", "player", "terrain", "building", "npc", "warp", "trigger", "memory", "report"],
        },
        "authority": {
            "bootstrap": "/api/v1/workbench/bootstrap",
            "events": "/api/v1/workbench/events",
            "evidence": "/api/v1/workbench/evidence",
            "runtime_health": "/api/v1/runtime/health",
            "runtime_snapshot": "/api/v1/runtime/snapshot",
            "player_cached": "/api/v1/map/v6/player/live",
            "player_explicit_discovery": "/api/v1/player/runtime",
            "world_scene": "/api/v1/map/v6/scene/current",
            "world_inspect": "/api/v1/lab/inspect",
            "runtime_actors": "/api/v1/lab/actors/live",
            "dialogue_history": "/api/dialogue/history",
            "navigation": "/api/v1/lab/navigation/path",
            "calibration": "/api/v1/lab/calibration/status",
            "bounded_memory_tools": "/frontend/memory-tracer.html",
        },
        "rules": [
            "Workbench aggregation never implies semantic success from HTTP connectivity.",
            "Unresolved is a valid reverse-engineering state, not a transport error.",
            "Heavy RAM discovery remains explicit and operator initiated.",
            "ROM static facts and RAM runtime facts remain separately attributable.",
        ],
    }
