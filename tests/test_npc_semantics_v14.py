from backend.black2.world.actor_binding import bind_static_npcs_to_runtime, merge_scene_npcs
from backend.black2.world.npc_classifier import NPCClassifier


def test_movement_hint_does_not_invent_trainer_state():
    result = NPCClassifier().classify_entity({
        "id": 4, "record_index": 4, "sprite_id": 20, "movement_id": 1,
        "script_id": 42, "flag_id": 8, "sight_raw": 5,
        "x": 10, "y": 0, "z": 10,
    }, 439)
    assert result["semantics"]["kind"] == "NPC_UNRESOLVED"
    assert result["semantics"]["trainer"]["is_trainer"] is None
    assert result["semantics"]["trainer"]["is_defeated"] is None
    assert result["semantics"]["trainer"]["will_battle"] is None
    assert result["semantics"]["trainer"]["sight_range"] == 5
    assert result["interaction"]["can_interact_now"] is None


def test_runtime_binding_survives_npc_movement_and_exposes_live_facing():
    rows = bind_static_npcs_to_runtime([
        {"id": 1, "record_index": 1, "script_id": 42, "flag_id": 8, "sprite_id": 20,
         "x": 10, "y": 0, "z": 10},
    ], [
        {"slot": 4, "actor_uid": 99, "address": "0x0223DFE4", "script_id": 42,
         "spawn_flag": 8, "model_id": 20, "grid": {"x": 12, "y": 0, "z": 10},
             "facing": "East", "effective_zone_id_candidate": 439, "same_current_scene": True},
    ], zone_id=439)
    runtime = rows[0]["runtime"]
    assert runtime["present"] is True
    assert runtime["grid"]["x"] == 12
    assert runtime["facing"] == "East"
    assert runtime["binding"]["status"] == "probable"


def test_same_xz_on_different_layer_is_not_strong_binding():
    rows = bind_static_npcs_to_runtime([
        {"id": 1, "record_index": 1, "script_id": 42, "flag_id": 8, "sprite_id": 20,
         "x": 10, "y": 0, "z": 10},
    ], [{
        "slot": 1, "actor_uid": 2, "script_id": 999, "spawn_flag": 999, "model_id": 99,
        "grid": {"x": 10, "y": 1, "z": 10}, "same_current_scene": True,
    }], zone_id=439)
    assert rows[0]["runtime"]["binding"]["status"] == "unresolved"


def test_coordinate_fallback_uses_event_axis_mapping_not_raw_event_order():
    rows = bind_static_npcs_to_runtime([{
        "id": 1, "record_index": 1,
        # ROM event: x=110, horizontal event.y=695, elevation event.z=1.
        "x": 110, "y": 695, "z": 1,
    }], [{
        "slot": 1, "actor_uid": 2,
        "grid": {"x": 110, "y": 1, "z": 695},
        "effective_zone_id_candidate": 439, "same_current_scene": True,
    }], zone_id=439)
    assert rows[0]["runtime"]["binding"]["status"] == "candidate"


def _scene_merge(static_by_zone, actors):
    return merge_scene_npcs(static_by_zone, actors)


def test_probable_binding_suppresses_only_its_static_candidate():
    merged = _scene_merge({439: [{
        "id": 1, "record_index": 2, "script_id": 42, "flag_id": 8, "sprite_id": 20,
        "x": 10, "y": 20, "z": 0,
    }]}, [{
        "slot": 4, "actor_uid": 99, "script_id": 42, "spawn_flag": 8, "model_id": 20,
        "grid": {"x": 11, "y": 0, "z": 20}, "effective_zone_id_candidate": 439, "same_current_scene": True,
    }])
    row = merged["npcs"][0]
    assert row["binding"]["status"] == "probable"
    assert row["suppress_static_candidate"] is True
    assert merged["suppressed_static_entity_ids"] == ["rom-npc:z439:id1:r2"]


def test_coordinate_candidate_does_not_suppress_static_marker():
    merged = _scene_merge({439: [{"id": 1, "record_index": 2, "x": 10, "y": 20, "z": 0}]}, [{
        "slot": 4, "actor_uid": 99, "grid": {"x": 10, "y": 0, "z": 20}, "effective_zone_id_candidate": 439, "same_current_scene": True,
    }])
    row = merged["npcs"][0]
    assert row["binding"]["status"] == "candidate"
    assert row["suppress_static_candidate"] is False
    assert merged["suppressed_static_entity_ids"] == []


def test_actor_disappearance_restores_static_candidate_by_returning_no_suppression():
    static = {439: [{
        "id": 1, "record_index": 2, "script_id": 42, "flag_id": 8, "sprite_id": 20,
        "x": 10, "y": 20, "z": 0,
    }]}
    assert _scene_merge(static, [
        {"slot": 4, "actor_uid": 99, "script_id": 42, "spawn_flag": 8, "model_id": 20,
         "grid": {"x": 10, "y": 0, "z": 20}, "effective_zone_id_candidate": 439, "same_current_scene": True},
    ])["suppressed_static_entity_ids"]
    assert _scene_merge(static, [])["suppressed_static_entity_ids"] == []


def test_actor_does_not_hide_static_candidate_from_another_zone():
    merged = _scene_merge({439: [{
        "id": 1, "record_index": 2, "script_id": 42, "flag_id": 8, "sprite_id": 20,
        "x": 10, "y": 20, "z": 0,
    }], 446: [{
        "id": 1, "record_index": 2, "script_id": 42, "flag_id": 8, "sprite_id": 20,
        "x": 10, "y": 20, "z": 0,
    }]}, [{
        "slot": 4, "actor_uid": 99, "script_id": 42, "spawn_flag": 8, "model_id": 20,
        "grid": {"x": 10, "y": 0, "z": 20}, "effective_zone_id_candidate": 439,
        "same_current_scene": True,
    }])
    by_zone = {row["source_zone_id"]: row for row in merged["npcs"]}
    assert by_zone[439]["suppress_static_candidate"] is True
    assert by_zone[446]["suppress_static_candidate"] is False


def test_candidate_before_probable_cannot_consume_the_runtime_actor():
    rows = bind_static_npcs_to_runtime([
        {"id": 1, "record_index": 1, "x": 10, "y": 20, "z": 0},
        {"id": 2, "record_index": 2, "script_id": 42, "flag_id": 8, "sprite_id": 20,
         "x": 11, "y": 20, "z": 0},
    ], [{
        "slot": 4, "actor_uid": 99, "script_id": 42, "spawn_flag": 8, "model_id": 20,
        "grid": {"x": 10, "y": 0, "z": 20}, "effective_zone_id_candidate": 439,
        "same_current_scene": True,
    }], zone_id=439)
    assert rows[0]["runtime"]["binding"]["status"] == "candidate"
    assert rows[1]["runtime"]["binding"]["status"] == "probable"


def test_raw_zero_actor_without_effective_zone_cannot_suppress_any_connected_zone():
    merged = _scene_merge({439: [{
        "id": 1, "record_index": 1, "script_id": 42, "flag_id": 8, "sprite_id": 20,
        "x": 10, "y": 20, "z": 0,
    }], 446: [{
        "id": 2, "record_index": 2, "script_id": 42, "flag_id": 8, "sprite_id": 20,
        "x": 10, "y": 20, "z": 0,
    }]}, [{
        "slot": 4, "actor_uid": 99, "zone_id": 0, "script_id": 42, "spawn_flag": 8,
        "model_id": 20, "grid": {"x": 10, "y": 0, "z": 20}, "same_current_scene": True,
    }])
    assert merged["suppressed_static_entity_ids"] == []
    assert all(row["binding"]["status"] == "unresolved" for row in merged["npcs"])
