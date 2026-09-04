"""Stable browser-facing runtime API.

Every UI module should prefer these endpoints.  They are cache reads and never
mistake a semantic decoder failure for a disconnected HTTP/Bridge transport.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ..observer.logger import observer_logger
from ..runtime.config import runtime_config
from ..runtime.control_log import RUNTIME_MONITOR_VERSION, runtime_control_log
from ..runtime.hub import RuntimeHub
from ..runtime.versions import (
    PROTOCOL_VERSIONS,
    RUNTIME_RELEASE_VERSION,
    component_version_report,
)

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime-hub"])
RUNTIME_CONTROL_VERSION = RUNTIME_RELEASE_VERSION
_hub: RuntimeHub | None = None
_restart_token = secrets.token_urlsafe(32)
_restart_scheduled = False


def configure_runtime_routes(hub: RuntimeHub) -> None:
    global _hub
    _hub = hub


def _runtime_hub() -> RuntimeHub:
    if _hub is None:
        raise RuntimeError("runtime routes are not configured")
    return _hub


def _is_loopback(request: Request) -> bool:
    return request.client is not None and request.client.host in {"127.0.0.1", "::1", "localhost"}


def _restart_command() -> list[str]:
    project_root = Path(__file__).resolve().parents[3]
    launcher = project_root / "run_runtime.py"
    return [
        sys.executable,
        str(launcher),
        "--host", runtime_config.http_host,
        "--port", str(runtime_config.http_port),
        "--bridge-host", runtime_config.bridge_host,
        "--bridge-port", str(runtime_config.bridge_port),
        "--start-delay", "2.0",
    ]


@router.get("/health")
async def health(hub: RuntimeHub = Depends(_runtime_hub)) -> dict[str, Any]:
    return hub.health()


@router.get("/snapshot")
async def snapshot(hub: RuntimeHub = Depends(_runtime_hub)) -> dict[str, Any]:
    return hub.snapshot()


@router.post("/refresh")
async def refresh(hub: RuntimeHub = Depends(_runtime_hub)) -> dict[str, Any]:
    return await hub.sample_once()


@router.get("/control")
async def control(request: Request) -> dict[str, Any]:
    """Return the local-only capability token for a deliberate backend restart."""
    if not _is_loopback(request):
        raise HTTPException(status_code=403, detail="runtime control is available only from localhost")
    return {
        "component": "runtime-control",
        "version": RUNTIME_CONTROL_VERSION,
        "capabilities": {"restart_backend": True, "runtime_monitor": True},
        "restart_token": _restart_token,
        "restart_after_seconds": 2.0,
    }


@router.get("/control/status")
async def control_status(request: Request, hub: RuntimeHub = Depends(_runtime_hub)) -> dict[str, Any]:
    """Return only locally observable service health and lifecycle metadata."""
    if not _is_loopback(request):
        raise HTTPException(status_code=403, detail="runtime control is available only from localhost")
    health = hub.health()
    rom_value = os.getenv("BLACK2_ROM_PATH")
    rom_path = Path(rom_value) if rom_value else None
    return {
        "component": "runtime-monitor",
        "version": RUNTIME_MONITOR_VERSION,
        "pid": os.getpid(),
        "restart_scheduled": _restart_scheduled,
        "restart_parent_pid": os.getenv("BLACK2_RESTART_PARENT_PID"),
        "ports": runtime_config.public_schema(),
        "health": health,
        "rom": {
            "configured": bool(rom_path),
            "available": bool(rom_path and rom_path.is_file()),
            "file_name": rom_path.name if rom_path else None,
        },
        "log_file": "logs/runtime_control.jsonl",
    }


@router.get("/control/logs")
async def control_logs(request: Request, limit: int = 100) -> dict[str, Any]:
    if not _is_loopback(request):
        raise HTTPException(status_code=403, detail="runtime control is available only from localhost")
    entries = runtime_control_log.recent(limit)
    return {
        "component": "runtime-monitor",
        "version": RUNTIME_MONITOR_VERSION,
        "count": len(entries),
        "entries": entries,
    }


@router.get("/versions")
async def versions(request: Request, hub: RuntimeHub = Depends(_runtime_hub)) -> dict[str, Any]:
    """Report expected and observed versions for every runtime component."""
    if not _is_loopback(request):
        raise HTTPException(status_code=403, detail="runtime versions are available only from localhost")
    bridge_connected = bool(hub.client.is_connected)
    bridge_version = getattr(hub.transport, "bridge_version", None)
    components = component_version_report(
        bridge_version=bridge_version,
        bridge_connected=bridge_connected,
    )
    return {
        "format": "black2-component-version-report/v1",
        "release": RUNTIME_RELEASE_VERSION,
        "status": "compatible" if all(item["status"] == "compatible" for item in components) else "attention",
        "components": components,
        "protocols": list(PROTOCOL_VERSIONS),
        "policy": {
            "scheme": "semantic-versioning",
            "major": "breaking component/API change",
            "minor": "backward-compatible capability",
            "patch": "backward-compatible correction",
        },
    }


@router.post("/restart")
async def restart(
    request: Request,
    x_runtime_restart_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Start a replacement ``run_runtime.py`` then retire this local process."""
    global _restart_scheduled
    if not _is_loopback(request):
        raise HTTPException(status_code=403, detail="runtime control is available only from localhost")
    if not x_runtime_restart_token or not secrets.compare_digest(x_runtime_restart_token, _restart_token):
        raise HTTPException(status_code=403, detail="invalid runtime restart token")
    if _restart_scheduled:
        raise HTTPException(status_code=409, detail="runtime restart is already scheduled")

    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        replacement_env = os.environ.copy()
        replacement_env["BLACK2_RESTART_PARENT_PID"] = str(os.getpid())
        subprocess.Popen(
            _restart_command(),
            cwd=str(Path(__file__).resolve().parents[3]),
            env=replacement_env,
            creationflags=flags,
        )
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"could not start replacement runtime: {exc}") from exc

    _restart_scheduled = True
    observer_logger.log_event(
        "runtime_restart_requested",
        f"component=runtime-control version={RUNTIME_CONTROL_VERSION} result=replacement_started",
    )
    runtime_control_log.record(
        "backend_restart_requested",
        "replacement_started",
        http_port=runtime_config.http_port,
        bridge_port=runtime_config.bridge_port,
    )

    async def retire_current_process() -> None:
        await asyncio.sleep(0.5)
        # The replacement waits before binding; this exits only after its
        # accepted response is sent and lets BizHawk reconnect automatically.
        runtime_control_log.record("backend_restart_retiring", "process_exit")
        os._exit(0)

    asyncio.create_task(retire_current_process(), name="black2-runtime-restart")
    return {
        "ok": True,
        "component": "runtime-control",
        "version": RUNTIME_CONTROL_VERSION,
        "result": "replacement_started",
        "browser_reload_after_seconds": 4.0,
    }


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
            "runtime_control": "/api/v1/runtime/control",
            "runtime_monitor_status": "/api/v1/runtime/control/status",
            "runtime_monitor_logs": "/api/v1/runtime/control/logs",
            "component_versions": "/api/v1/runtime/versions",
            "workbench_bootstrap": "/api/v1/workbench/bootstrap",
            "workbench_schema": "/api/v1/workbench/schema",
            "workbench_events": "/api/v1/workbench/events",
        },
        "components": {
            "runtime_control": {
                "version": RUNTIME_CONTROL_VERSION,
                "capabilities": {"restart_backend": True, "runtime_monitor": True},
            },
            "runtime_monitor": {"version": RUNTIME_MONITOR_VERSION, "capabilities": {"persistent_lifecycle_log": True}},
            "version_registry": {"version": RUNTIME_RELEASE_VERSION, "capabilities": {"expected_observed_comparison": True}},
        },
        "frontend_rule": "Never infer Backend/Bridge offline from a semantic/map/player request failure.",
        "workbench_rule": "Workbench aggregation is cache-first; heavy RAM discovery stays explicit and operator initiated.",
    }
