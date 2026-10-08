from backend.black2.runtime.npc_battle_history import NpcBattleHistory, diff_status
from backend.black2.world.npc_battle_status import build_npc_battle_status


def _static(*, record_index=2, sight=4):
    return {
        "id": f"zone:446:npc:{record_index}",
        "record_index": record_index,
        "kind": "npc",
        "name": "NPC_UNRESOLVED",
        "coordinate": {"space": "gen5-field-grid-v1", "zone_id": 446, "x": 178, "y": 0, "z": 649},
        "sprite_id": 9,
        "script_id": 3175,
        "flag_id": 123,
        "trainer": {"sight_range": sight, "is_trainer": None, "is_defeated": None},
        "rom": {"sight_raw": sight},
    }


def _actor(*, present=True, flags=3):
    return {
        "slot": 2,
        "address": "0x0223DDE4",
        "actor_uid": 77,
        "model_id": 9,
        "zone_id": 446,
        "same_current_scene": True,
        "grid": {"x": 178, "y": 0, "z": 649},
        "script_id": 3175,
        "spawn_flag": 123,
        "event_type": 1,
        "facing": "South",
        "raw": {
            "address": "0x0223DDE4",
            "flags_raw": flags,
            "spawn_flag_raw": 123,
            "script_id_raw": 3175,
            "face_dir_raw": 2,
            "next_acmd_raw": 9,
        },
        "_present": present,
    }


def test_sight_candidate_keeps_defeat_unknown_and_preserves_raw_actor_fields():
    result = build_npc_battle_status(
        [_static()], [_actor()], zone_id=446,
        zone_candidates=[{"trainer_id": 63, "name": "大辅"}],
        flags={"status": "unresolved", "contents_known": False},
        player={"position": {"grid": {"x": 177, "y": 0, "z": 649}}},
        frame=100,
    )
    row = result["npcs"][0]
    assert row["capability"]["status"] == "line_of_sight_battle_candidate"
    assert row["lifecycle"]["defeat_status"] == "unknown"
    assert row["runtime"]["raw"]["flags_raw"] == 3
    assert row["runtime"]["raw"]["script_id_raw"] == 3175
    assert result["probe_candidates"] == [row["npc_id"]]


def test_actor_absence_does_not_promote_defeat():
    result = build_npc_battle_status(
        [_static(sight=0)], [], zone_id=446,
        zone_candidates=[{"trainer_id": 63}],
        flags={"status": "unresolved", "contents_known": False},
    )
    row = result["npcs"][0]
    assert row["capability"]["status"] == "zone_script_battle_candidate_unmapped"
    assert row["runtime"]["present"] is None
    assert row["lifecycle"]["defeat_status"] == "unknown"


def test_before_after_diff_reports_raw_lifecycle_change_without_victory_claim():
    before = build_npc_battle_status(
        [_static()], [_actor(flags=3)], zone_id=446,
        zone_candidates=[{"trainer_id": 63}],
        flags={"status": "unresolved", "contents_known": False},
    )
    after = build_npc_battle_status(
        [_static()], [_actor(flags=7)], zone_id=446,
        zone_candidates=[{"trainer_id": 63}],
        flags={"status": "unresolved", "contents_known": False},
    )
    diff = diff_status(before, after)
    assert diff["status"] == "changed"
    assert diff["changes"][0]["raw_changes"]["flags_raw"] == {"before": 3, "after": 7}
    assert diff["changes"][0]["defeat_status_after"] == "unknown"


def test_history_compacts_to_selected_npc_and_labels_dialogue_without_battle(tmp_path):
    before = build_npc_battle_status([_static()], [_actor()], zone_id=446)
    after = build_npc_battle_status([_static()], [_actor(flags=7)], zone_id=446)
    path = tmp_path / "npc.ndjson"
    history = NpcBattleHistory(path, max_entries=2)
    result = history.record(
        before, after,
        npc_ids=["zone:446:npc:2"],
        battle_before={"active": False},
        battle_after={"active": False, "overlays": {"dialogue": {"active": True}}},
    )
    item = result["record"]
    assert item["probe_outcome"] == "dialogue_observed_without_battle"
    assert item["before"]["npcs"][0]["npc_id"] == "zone:446:npc:2"
    assert len(item["after"]["npcs"]) == 1
    assert item["diff"]["changes"][0]["raw_changes"]["flags_raw"] == {"before": 3, "after": 7}


def test_history_latest_by_npc_exposes_probe_meaning_and_raw_diff(tmp_path):
    before = build_npc_battle_status([_static()], [_actor(flags=3)], zone_id=446)
    after = build_npc_battle_status([_static()], [_actor(flags=9)], zone_id=446)
    history = NpcBattleHistory(tmp_path / "npc.ndjson", max_entries=4)
    history.record(
        before, after,
        npc_ids=["zone:446:npc:2"],
        battle_before={"active": False},
        battle_after={"active": False},
        dialogue_before={"active": False},
        dialogue_after={"active": True, "text": "普通对话"},
    )
    latest = history.latest_by_npc()["zone:446:npc:2"]
    assert latest["probe_count"] == 1
    assert latest["probe_outcome"] == "dialogue_observed_without_battle"
    assert "不等于该 NPC 永远没有战斗" in latest["meaning"]
    assert latest["diff"]["raw_changes"]["flags_raw"] == {"before": 3, "after": 9}
    assert latest["dialogue"]["after"]["text"] == "普通对话"


def test_history_does_not_call_existing_dialogue_an_npc_probe(tmp_path):
    before = build_npc_battle_status([_static()], [_actor()], zone_id=446)
    after = build_npc_battle_status([_static()], [_actor()], zone_id=446)
    history = NpcBattleHistory(tmp_path / "npc.ndjson")
    item = history.record(
        before, after,
        npc_ids=["zone:446:npc:2"],
        battle_before={"active": False},
        battle_after={"active": False},
        dialogue_before={"active": True, "text": "已有对话"},
        dialogue_after={"active": True, "text": "已有对话"},
    )["record"]
    assert item["probe_outcome"] == "precondition_modal_active"
    assert "探测开始前已经存在对话" in history.latest_by_npc()["zone:446:npc:2"]["meaning"]


def test_history_does_not_call_failed_navigation_no_battle(tmp_path):
    before = build_npc_battle_status([_static()], [_actor()], zone_id=446)
    after = build_npc_battle_status([_static()], [_actor()], zone_id=446)
    history = NpcBattleHistory(tmp_path / "npc.ndjson")
    item = history.record(
        before, after,
        npc_ids=["zone:446:npc:2"],
        battle_before={"active": False},
        battle_after={"active": False},
        probe_status="failed",
        probe_failure={"code": "AUTOMATION_INTERACTION_STAND_NOT_FOUND"},
    )["record"]
    assert item["probe_outcome"] == "probe_failed"
    latest = history.latest_by_npc()["zone:446:npc:2"]
    assert latest["probe_failure"]["code"] == "AUTOMATION_INTERACTION_STAND_NOT_FOUND"
    assert "探测未完成" in latest["meaning"]
