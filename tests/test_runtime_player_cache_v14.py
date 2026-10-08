"""Regression tests for PlayerRuntime cache ownership and refresh behavior."""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

from backend.black2.runtime.hub import RuntimeHub
from backend.black2.world import native_map
from backend.black2.world.runtime_player_state import PlayerRuntimeService


def _resolved_sample(*, frame: int = 100) -> dict:
    return {
        "format": "black2-runtime-player-live/v3",
        "status": "resolved",
        "confidence": "probable",
        "frame": frame,
        "zone_id": 446,
        "position": {
            "grid": {"x": 141, "y": 2, "z": 662},
            "world": {"x": 2256.0, "y": 32.0, "z": 10592.0},
        },
        "orientation": {"verified": True, "facing": "South"},
        "locomotion": {"phase": "Idle", "transport_mode": "OnFoot"},
    }


def _unresolved(reason: str = "cached field chain was unavailable") -> dict:
    return {
        "format": "black2-runtime-player-live/v3",
        "status": "unresolved",
        "confidence": "unresolved",
        "reason": reason,
    }


class FakeLocator:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.invalidate_calls = 0

    async def sample_player(self, _reader, *, allow_discovery: bool = False) -> dict:
        del allow_discovery
        return copy.deepcopy(self.responses.pop(0))

    def invalidate(self) -> None:
        self.invalidate_calls += 1


class FakeReader:
    def __init__(self, *, session_id: str | None, connected: bool | None = True) -> None:
        self.client = SimpleNamespace(
            is_connected=connected,
            transport=SimpleNamespace(session_id=session_id),
        )


def test_background_miss_retains_explicit_discovery_for_same_bridge_session(monkeypatch) -> None:
    locator = FakeLocator([
        _resolved_sample(),
        _unresolved("background cache read missed"),
        _unresolved("state-engine cache read missed"),
    ])
    service = PlayerRuntimeService(locator=locator)
    reader = FakeReader(session_id="bridge-a")
    monkeypatch.setattr(native_map, "player_runtime_service", service)

    async def exercise() -> tuple[dict, dict, native_map.LiveMapState]:
        explicit = await service.sample(reader, allow_discovery=True)
        background = await service.sample(reader, allow_discovery=False)
        live = await native_map.read_live_map_state(reader, force_sample=True, allow_discovery=False)
        return explicit, background, live

    explicit, background, live = asyncio.run(exercise())

    assert explicit["status"] == "resolved"
    assert background["status"] == "resolved"
    assert background["position"]["grid"] == {"x": 141, "y": 2, "z": 662}
    assert background["cache"] == {
        "refresh_status": "retained_after_failed_background_refresh",
        "last_failure": {"status": "unresolved", "reason": "background cache read missed"},
        "session_bound": True,
    }
    assert service.latest is not None
    assert service.latest["status"] == "resolved"
    assert service.latest["position"]["grid"] == {"x": 141, "y": 2, "z": 662}
    assert live.verified is True
    assert (live.x, live.y, live.elevation) == (141, 662, 2)
    assert service.last_refresh_failure == {"status": "unresolved", "reason": "state-engine cache read missed"}


def test_session_change_invalidates_player_cache_instead_of_reusing_old_coordinates() -> None:
    locator = FakeLocator([_resolved_sample(), _unresolved("new attachment needs explicit discovery")])
    service = PlayerRuntimeService(locator=locator)
    reader = FakeReader(session_id="bridge-a")

    async def exercise() -> dict:
        await service.sample(reader, allow_discovery=True)
        reader.client.transport.session_id = "bridge-b"
        return await service.sample(reader, allow_discovery=False)

    result = asyncio.run(exercise())

    assert result["status"] == "unresolved"
    assert service.latest == result
    assert service.latest_session_id is None
    assert locator.invalidate_calls == 1


def test_disconnected_bridge_invalidates_cached_player_location_without_sampling() -> None:
    locator = FakeLocator([_resolved_sample()])
    service = PlayerRuntimeService(locator=locator)
    reader = FakeReader(session_id="bridge-a")

    async def exercise() -> dict:
        await service.sample(reader, allow_discovery=True)
        reader.client.is_connected = False
        return await service.sample(reader, allow_discovery=False)

    result = asyncio.run(exercise())

    assert result["status"] == "unresolved"
    assert "disconnected" in result["reason"]
    assert service.latest is None
    assert locator.invalidate_calls == 1


def test_runtime_hub_clears_player_cache_while_bridge_is_disconnected(monkeypatch) -> None:
    locator = FakeLocator([])
    service = PlayerRuntimeService(locator=locator)
    service.latest = _resolved_sample()
    service.latest_session_id = "bridge-a"
    monkeypatch.setattr("backend.black2.runtime.hub.player_runtime_service", service)
    hub = RuntimeHub(
        client=SimpleNamespace(is_connected=False),
        reader=None,
        state_engine=None,
        transport=SimpleNamespace(last_heartbeat=0, last_frame=0, bridge_version="test", session_id="bridge-a"),
    )

    snapshot = asyncio.run(hub.sample_once())

    assert service.latest is None
    assert locator.invalidate_calls == 1
    assert snapshot["player"] is None
