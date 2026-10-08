import pytest
from backend.black2.world.player_capabilities import evaluate_capabilities

def test_capabilities_on_foot_outdoor():
    caps = evaluate_capabilities(
        ex_state_raw=0, # on foot
        zone_rules={"enable_cycling": True, "enable_running": True, "enable_escape_rope": False, "enable_fly_from": True},
        party_move_ids={19, 70, 57}, # Fly, Strength, Surf
        key_item_ids={448}, # Bicycle
        adjacent_features=set(),
    )
    d = caps.as_dict()
    assert d["movement_mode"] == "on_foot"
    assert d["legal_capabilities"]["running"] is True
    assert d["legal_capabilities"]["cycling"] is True
    assert d["legal_capabilities"]["fly"] is True
    assert d["legal_capabilities"]["surf"] is False # not near water
    assert d["legal_capabilities"]["strength"] is False # no boulder
    assert d["legal_capabilities"]["escape_rope"] is False # outdoor

def test_capabilities_cave_with_boulder():
    caps = evaluate_capabilities(
        ex_state_raw=0,
        zone_rules={"enable_cycling": False, "enable_running": True, "enable_escape_rope": True, "enable_fly_from": False},
        party_move_ids={70}, # Strength
        key_item_ids={448}, # Bicycle
        bag_item_ids={56}, # Escape Rope
        adjacent_features={"boulder"},
    )
    d = caps.as_dict()
    assert d["legal_capabilities"]["cycling"] is False # prohibited in cave
    assert d["legal_capabilities"]["strength"] is True # has strength & facing boulder
    assert d["legal_capabilities"]["escape_rope"] is True
    assert d["legal_capabilities"]["fly"] is False

def test_capabilities_in_water():
    caps = evaluate_capabilities(
        ex_state_raw=2, # surfing
        zone_rules={"enable_cycling": True, "enable_running": True, "enable_escape_rope": False, "enable_fly_from": True},
        party_move_ids={57, 127}, # Surf, Waterfall
        key_item_ids={448},
        adjacent_features={"waterfall"},
    )
    d = caps.as_dict()
    assert d["movement_mode"] == "surfing"
    assert d["legal_capabilities"]["cycling"] is False # cannot bike in water
    assert d["legal_capabilities"]["running"] is False # cannot run in water
    assert d["legal_capabilities"]["waterfall"] is True
