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


def _fact(value: Any, *, status: str, source: str, confidence: str,
          reason: str | None = None, raw: Any = None) -> dict[str, Any]:
    """Return one evidence-labelled environment fact.

    Environment values are consumed by autonomous clients, so a missing
    decoder must be visible as ``unresolved`` instead of being represented by
    a plausible-looking default (for example, clear weather or spring).
    """
    payload = {
        "status": status,
        "value": value,
        "source": source,
        "confidence": confidence,
    }
    if reason is not None:
        payload["reason"] = reason
    if raw is not None:
        payload["raw"] = raw
    return payload


def _environment_static_zone(zone_id: int | None) -> dict[str, Any]:
    """Read static Zone header metadata when the ROM provider is available."""
    if not isinstance(zone_id, int):
        return {"status": "unresolved", "reason": "current runtime Zone is unavailable"}
    try:
        # Import lazily to keep status routes usable in ROM-less/unit-test
        # environments and to avoid startup work for every status request.
        from .navigation_routes import navigation_static_provider
        provider = navigation_static_provider()
        rom = getattr(provider, "rom", None) if provider is not None else None
        header = rom.zone(zone_id) if rom is not None else None
        if header is None:
            raise RuntimeError("static ROM provider is unavailable")
        return {
            "status": "candidate",
            "zone_id": zone_id,
            "matrix_id": getattr(header, "matrix_id", None),
            "weather": _fact(
                getattr(header, "weather", None), status="candidate" if getattr(header, "weather", None) is not None else "unresolved",
                source="ROM ZoneHeader.env_flags", confidence="rom_record",
                reason="Zone header weather is a static default/candidate; runtime weather overrides are unresolved",
            ),
            "battle_background": _fact(
                getattr(header, "battle_bg", None), status="candidate" if getattr(header, "battle_bg", None) is not None else "unresolved",
                source="ROM ZoneHeader.flags_battle", confidence="rom_record",
                reason="Battle background ID is static metadata, not the active battle visual",
            ),
            "rules": {
                "running": bool(getattr(header, "enable_running", False)),
                "cycling": bool(getattr(header, "enable_cycling", False)),
                "fly_from": bool(getattr(header, "enable_fly_from", False)),
            },
        }
    except (AttributeError, FileNotFoundError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return {
            "status": "unavailable",
            "zone_id": zone_id,
            "reason": "static ROM ZoneHeader could not be decoded",
        }


def _environment_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    player = snapshot.get("player") if isinstance(snapshot.get("player"), dict) else {}
    locomotion = player.get("locomotion") if isinstance(player.get("locomotion"), dict) else {}
    runtime_env = player.get("environment") if isinstance(player.get("environment"), dict) else {}
    tile_raw = runtime_env.get("tile_under") if isinstance(runtime_env.get("tile_under"), dict) else {}
    props = player.get("props") if isinstance(player.get("props"), dict) else {}
    transport = snapshot.get("transport") if isinstance(snapshot.get("transport"), dict) else {}
    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
    player_position = player.get("position") if isinstance(player.get("position"), dict) else {}
    player_grid = player_position.get("grid") if isinstance(player_position.get("grid"), dict) else {}
    player_ready = (
        player.get("status") in {"resolved", "candidate"}
        and all(type(player_grid.get(axis)) is int for axis in ("x", "y", "z"))
    )
    current = bool(
        transport.get("bridge_connected") is True
        and runtime.get("semantic_status") == "ready"
        and player_ready
        and isinstance(snapshot.get("age_seconds"), (int, float))
        and 0 <= float(snapshot.get("age_seconds")) <= 3
    )
    source_frame = player.get("frame")
    # Same-frame attribution requires two explicit, comparable frame IDs.
    # If both are absent, equality of ``None`` is not evidence of alignment.
    source_frame_is_valid = type(source_frame) is int

    tile = {"status": "unresolved", "source": "FieldActor.TileClass/TileFlags", "confidence": "unresolved"}
    if type(tile_raw.get("class")) is int and type(tile_raw.get("flags")) is int:
        try:
            from ..world.tile_semantics import decode_tile_semantics
            decoded = decode_tile_semantics(tile_raw["class"], tile_raw["flags"])
            tile = {
                "status": "current_candidate" if current else "stale_candidate",
                "source": "FieldActor.TileClass/TileFlags",
                "confidence": "runtime_structure",
                "raw": {"class": tile_raw["class"], "flags": tile_raw["flags"]},
                "material": decoded["material"],
                "collision": decoded["collision"],
                "encounter": decoded.get("encounter"),
            }
        except (TypeError, ValueError):
            tile["reason"] = "runtime TileClass/TileFlags failed semantic decoding"
    props_status = str(props.get("status") or "unresolved")
    def prop_fact(key: str) -> dict[str, Any]:
        value = props.get(key)
        if type(value) is int and props_status in {"probable", "resolved", "candidate"}:
            props_frame = props.get("source_frame")
            same_frame = source_frame_is_valid and type(props_frame) is int and props_frame == source_frame
            return _fact(value, status="raw_candidate" if current and same_frame else "stale_candidate",
                         source=str(props.get("source") or "FieldPropSystem"), confidence="structure_candidate",
                         reason="Raw enum; label requires paired time/season validation", raw=value)
        return _fact(None, status="unresolved", source="FieldPropSystem", confidence="unresolved",
                     reason="FieldPropSystem day-part/season is not available in the current cached sample")

    zone_id = player.get("zone_id") if type(player.get("zone_id")) is int else None
    static_zone = _environment_static_zone(zone_id)
    battle = snapshot.get("battle") if isinstance(snapshot.get("battle"), dict) else {}
    return {
        "format": "black2-game-environment/v1",
        "status": "current" if current else "partial",
        "read_only": True,
        "writes_performed": False,
        "mutation_policy": "cache_reads_only",
        "frame": source_frame,
        "session_id": transport.get("session_id"),
        "runtime_observation": {
            "current": current,
            "snapshot_age_seconds": snapshot.get("age_seconds"),
            "source": "RuntimeHub cached PlayerRuntime; no bridge read initiated",
        },
        "coordinate": {
            "space": "gen5-field-grid-v1",
            "zone_id": zone_id,
            "grid": (player.get("position") or {}).get("grid") if isinstance(player.get("position"), dict) else None,
            "matrix_id": (player.get("global") or {}).get("matrix_id") if isinstance(player.get("global"), dict) else player.get("matrix_id"),
            "matrix_id_static_candidate": static_zone.get("matrix_id") if isinstance(static_zone, dict) else None,
        },
        "transport": {
            "mode": _fact(locomotion.get("transport_mode"), status="current_candidate" if current and locomotion.get("transport_mode") else "unresolved",
                           source="PlayerState.ExState", confidence="runtime_structure",
                           reason="OnFoot gait is separate from transport mode"),
            "phase": _fact(locomotion.get("phase"), status="current_candidate" if current and locomotion.get("phase") else "unresolved",
                           source="PlayerGrid/PlayerCore movement state", confidence="runtime_structure"),
            "gait": _fact(locomotion.get("gait"), status="calibrated" if locomotion.get("gait_confidence") == "calibrated" else "unresolved",
                          source="PlayerRuntime temporal calibration", confidence=locomotion.get("gait_confidence", "unresolved"),
                          reason="Walk vs run needs paired labeled speed samples"),
        },
        "tile": tile,
        "overworld": {
            "time_of_day": prop_fact("day_part"),
            "previous_time_of_day": prop_fact("previous_day_part"),
            "time_changed": prop_fact("day_part_changed"),
            "season": prop_fact("season"),
            "zone_weather": (static_zone.get("weather") if isinstance(static_zone, dict) else None) or _fact(
                None, status="unresolved", source="ROM ZoneHeader", confidence="unresolved",
                reason="Zone weather metadata unavailable",
            ),
        },
        "zone_static": static_zone,
        "battle": {
            "active": battle.get("active"),
            "weather": _fact(None, status="unresolved", source="BattleRuntime", confidence="unresolved",
                              reason="Active battle weather/field overrides are not decoded"),
            "terrain": _fact(None, status="unresolved", source="BattleRuntime", confidence="unresolved",
                              reason="Battle visual terrain/field effects are not decoded"),
            "policy": "Battle state overrides overworld weather; unresolved values must not be guessed from ZoneHeader.",
        },
        "evidence": {
            "verified": False,
            "confidence": "candidate" if current else "unresolved",
            "limitations": [
                "Static ZoneHeader weather/background are candidates, not live battle effects.",
                "Raw day-part/season enums require paired in-game time validation.",
                "No environment endpoint performs input or RAM writes.",
            ],
        },
    }


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


@router.get("/environment")
async def current_game_environment() -> dict[str, Any]:
    """Expose transport, terrain, time/season and weather with provenance."""
    return _environment_payload(_snapshot())


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
