import pytest
from backend.black2.domain import (
    CommandStatus,
    Confidence,
    Direction,
    EntityId,
    FlagId,
    GameMode,
    GridCoord,
    ItemId,
    MatrixId,
    MoveId,
    ScriptId,
    Severity,
    SpeciesId,
    WorldCoord,
    ZoneId,
)


def test_zone_id_validation():
    z = ZoneId(439)
    assert z == 439
    assert isinstance(z, int)
    assert repr(z) == "ZoneId(439)"

    with pytest.raises(ValueError):
        ZoneId(-1)
    with pytest.raises(ValueError):
        ZoneId(70000)


def test_direction_helpers():
    d = Direction.from_str("north")
    assert d == Direction.NORTH
    assert d.dx == 0
    assert d.dz == -1
    assert d.opposite == Direction.SOUTH

    east = Direction.EAST
    assert east.dx == 1
    assert east.dz == 0
    assert east.opposite == Direction.WEST


def test_game_mode():
    assert GameMode.from_str("battle") == GameMode.BATTLE
    assert GameMode.from_str("overworld") == GameMode.OVERWORLD
    assert GameMode.from_str("invalid") == GameMode.UNKNOWN


def test_confidence():
    assert Confidence.VERIFIED.is_verified is True
    assert Confidence.CANDIDATE.is_verified is False


def test_coordinates():
    g1 = GridCoord(10, 0, 20)
    g2 = GridCoord(13, 0, 24)
    assert g1.distance_manhattan(g2) == 3 + 4
    assert g1.as_tuple() == (10, 0, 20)
    assert g1.as_dict() == {"x": 10, "y": 0, "z": 20}

    w1 = WorldCoord(0.0, 0.0, 0.0)
    w2 = WorldCoord(3.0, 0.0, 4.0)
    assert w1.distance(w2) == 5.0
