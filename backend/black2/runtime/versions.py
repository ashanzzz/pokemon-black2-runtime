"""Single source of truth for deployable component and protocol versions.

Component versions describe independently reloadable code.  Protocol/schema
versions describe data compatibility and therefore do not automatically change
with every application release.
"""
from __future__ import annotations

from typing import Any


RUNTIME_RELEASE_VERSION = "9.0.0"
WORLD3D_SCENE_VERSION = "9.0.0"
ORIGINAL_MAP_UI_VERSION = "9.0.0"
BIZHAWK_BRIDGE_VERSION = "1.8.0-world-lab"

COMPONENT_VERSIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "runtime_launcher",
        "name": "run_runtime.py",
        "kind": "process",
        "expected_version": RUNTIME_RELEASE_VERSION,
    },
    {
        "id": "fastapi_backend",
        "name": "FastAPI Backend",
        "kind": "service",
        "expected_version": RUNTIME_RELEASE_VERSION,
    },
    {
        "id": "runtime_control",
        "name": "Runtime Control API",
        "kind": "service",
        "expected_version": RUNTIME_RELEASE_VERSION,
    },
    {
        "id": "runtime_monitor",
        "name": "Runtime Monitor UI/API",
        "kind": "web",
        "expected_version": RUNTIME_RELEASE_VERSION,
    },
    {
        "id": "bizhawk_bridge",
        "name": "BizHawk Lua Bridge",
        "kind": "bridge",
        "expected_version": BIZHAWK_BRIDGE_VERSION,
    },
    {
        "id": "world3d_scene",
        "name": "3D Scene Contract",
        "kind": "protocol",
        "expected_version": WORLD3D_SCENE_VERSION,
    },
    {
        "id": "original_map_ui",
        "name": "Workbench UI",
        "kind": "web",
        "expected_version": ORIGINAL_MAP_UI_VERSION,
    },
    {
        "id": "workbench_api",
        "name": "Workbench Aggregation API",
        "kind": "service",
        "expected_version": RUNTIME_RELEASE_VERSION,
    },
)

PROTOCOL_VERSIONS: tuple[dict[str, str], ...] = (
    {"id": "runtime_health", "name": "Runtime Health", "version": "black2-runtime-health/v2"},
    {"id": "runtime_snapshot", "name": "Runtime Snapshot", "version": "black2-runtime-snapshot/v4"},
    {"id": "world3d_scene_schema", "name": "World3D Scene Schema", "version": "black2-world3d-scene/v6"},
    {"id": "world_lab", "name": "World Lab Diagnostics", "version": "black2-world-lab/v1"},
    {"id": "workbench_ui", "name": "Workbench UI Contract", "version": "black2-workbench-ui-contract/v1"},
    {"id": "observed_navigation", "name": "Observed Layered Navigation", "version": "black2-observed-navigation/v1"},
    {"id": "universal_snapshot", "name": "Universal Snapshot", "version": "universal_snapshot/v2"},
    {"id": "runtime_world_export", "name": "Runtime World Export", "version": "pokemon_black2_runtime_world_export/v1"},
)


def component_version_report(*, bridge_version: str | None, bridge_connected: bool) -> list[dict[str, Any]]:
    """Return expected and process-observed versions with closed compatibility states."""
    observed = {
        "runtime_launcher": RUNTIME_RELEASE_VERSION,
        "fastapi_backend": RUNTIME_RELEASE_VERSION,
        "runtime_control": RUNTIME_RELEASE_VERSION,
        "runtime_monitor": RUNTIME_RELEASE_VERSION,
        "bizhawk_bridge": bridge_version if bridge_connected else None,
        "world3d_scene": WORLD3D_SCENE_VERSION,
        "original_map_ui": ORIGINAL_MAP_UI_VERSION,
        "workbench_api": RUNTIME_RELEASE_VERSION,
    }
    report: list[dict[str, Any]] = []
    for definition in COMPONENT_VERSIONS:
        item = dict(definition)
        actual = observed.get(item["id"])
        item["observed_version"] = actual
        item["status"] = (
            "unavailable"
            if actual is None
            else "compatible"
            if actual == item["expected_version"]
            else "mismatch"
        )
        report.append(item)
    return report
