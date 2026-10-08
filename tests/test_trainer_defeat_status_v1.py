"""Unit tests for B2W2 IREJ engine trainer defeat flag resolution and radar integration."""
import pytest
from unittest.mock import MagicMock
from backend.black2.progression.state import (
    resolve_trainer_defeat_flag,
    is_event_flag_set,
    ProgressionStateService,
)
from backend.black2.api.navigation_routes import (
    _build_trainer_sight_rays,
    _radar_cell,
)


def test_resolve_trainer_defeat_flag():
    # 3000..4999 range: flag = script_id - 1480
    assert resolve_trainer_defeat_flag(3745) == 2265
    assert resolve_trainer_defeat_flag(3738) == 2258
    assert resolve_trainer_defeat_flag(3746) == 2266
    assert resolve_trainer_defeat_flag(3000) == 1520

    # >= 5000 range: flag = script_id - 3480
    assert resolve_trainer_defeat_flag(5000) == 1520
    assert resolve_trainer_defeat_flag(5100) == 1620

    # Non-trainer scripts (< 3000)
    assert resolve_trainer_defeat_flag(1) is None
    assert resolve_trainer_defeat_flag(2000) is None
    assert resolve_trainer_defeat_flag(0) is None
    assert resolve_trainer_defeat_flag(None) is None


def test_is_event_flag_set():
    # Construct a 383-byte buffer with flag 2265 set
    flag_bytes = bytearray(383)
    byte_idx = 2265 >> 3 # 283
    bit_idx = 2265 & 7   # 1
    flag_bytes[byte_idx] |= (1 << bit_idx)

    assert is_event_flag_set(2265, bytes(flag_bytes)) is True
    assert is_event_flag_set(2266, bytes(flag_bytes)) is False
    assert is_event_flag_set(2258, bytes(flag_bytes)) is False
    assert is_event_flag_set(None, bytes(flag_bytes)) is False
    assert is_event_flag_set(2265, None) is False


def test_progression_service_is_trainer_defeated():
    svc = ProgressionStateService()
    flag_bytes = bytearray(383)
    # Set flag 2265 (script 3745) and flag 2266 (script 3746)
    flag_bytes[2265 >> 3] |= (1 << (2265 & 7))
    flag_bytes[2266 >> 3] |= (1 << (2266 & 7))
    svc._raw_flag_bytes = bytes(flag_bytes)

    assert svc.is_trainer_defeated(3745) is True
    assert svc.is_trainer_defeated(3746) is True
    assert svc.is_trainer_defeated(3738) is False
    assert svc.is_trainer_defeated(1) is False


def test_build_trainer_sight_rays_omits_defeated_trainer():
    # Mock provider and ROM with NPC 0 (script 3745, sight 3)
    provider = MagicMock()
    z_obj = MagicMock()
    z_obj.entities_id = 308
    z_obj.matrix_id = 256
    provider.rom.zone.return_value = z_obj

    npc0 = {
        "id": 0, "record_index": 0,
        "sprite_id": 11, "script_id": 3745,
        "sight_raw": 3, "facing_id": 3, # East
        "x": 17, "y": 40,
    }
    provider.rom.entities.return_value = {"npcs": [npc0]}

    # Case 1: Without defeat flag set -> sight rays and trainer map ARE generated
    flag_bytes_undefeated = bytearray(383)
    sight_map, trainer_map, summaries = _build_trainer_sight_rays(
        provider, 457, 10, 25, 30, 45,
        flag_bytes=bytes(flag_bytes_undefeated),
    )
    assert (17, 40) in trainer_map
    assert trainer_map[(17, 40)]["symbol"] == "T"
    assert len(sight_map) > 0

    # Case 2: With flag 2265 set -> trainer is defeated, NO sight rays, NO trainer entry!
    flag_bytes_defeated = bytearray(383)
    flag_bytes_defeated[2265 >> 3] |= (1 << (2265 & 7))
    sight_map, trainer_map, summaries = _build_trainer_sight_rays(
        provider, 457, 10, 25, 30, 45,
        flag_bytes=bytes(flag_bytes_defeated),
    )
    assert (17, 40) not in trainer_map
    assert len(sight_map) == 0
    assert len(summaries) == 0


def test_radar_cell_renders_defeated_trainer_as_peaceful_npc():
    provider = MagicMock()
    provider.surface_at.return_value = {
        "cell": {"material": {"kind": "road"}, "static_blocked": False},
        "surfaces": [{"material": {"kind": "road"}, "static_blocked": False, "height": {"chunk_relative_world_y": 0.0}}],
        "walkable": True,
    }
    provider.preview_surface_at.return_value = provider.surface_at.return_value

    flag_bytes_defeated = bytearray(383)
    flag_bytes_defeated[2265 >> 3] |= (1 << (2265 & 7))

    runtime_actors = [{
        "slot": 0, "actor_uid": 0, "is_player": False,
        "model_id": 11, "script_id": 3745,
        "grid": {"x": 17, "y": 0, "z": 40},
        "facing": "East", "zone_id": 457, "effective_zone_id_candidate": 457,
    }]

    # When flag is set, cell at (17, 40) should be NPC 'N', NOT 'T'!
    cell = _radar_cell(
        provider, 457, 17, 0, 40,
        runtime_actors=runtime_actors,
        flag_bytes=bytes(flag_bytes_defeated),
    )
    assert cell["symbol"] == "N"
    assert "对战训练家" not in cell["kind"]
