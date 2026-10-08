import tempfile
from pathlib import Path
from backend.black2.runtime.session_store import SessionStore


def test_session_lifecycle():
    store = SessionStore(":memory:")
    sid = store.start_session(rom_code="IREJ", app_version="v18")
    assert sid.startswith("session_")
    assert store.active_session_id == sid

    stats = store.get_stats()
    assert stats["sessions"] == 1
    assert stats["active_session"] == sid

    store.end_session()
    assert store.active_session_id is None


def test_event_recording_and_queries():
    store = SessionStore(":memory:")
    sid = store.start_session()

    seq1 = store.record_event({
        "type": "player.moved",
        "summary": "Moved to (10, 20)",
        "frame": 100,
        "data": {"x": 10, "z": 20},
    })
    seq2 = store.record_event({
        "type": "battle.started",
        "summary": "Wild encounter",
        "frame": 150,
        "data": {"species": 501},
    })

    assert seq1 == 1
    assert seq2 == 2

    # Query all
    events = store.get_events(since_seq=0)
    assert len(events) == 2
    assert events[0]["type"] == "player.moved"
    assert events[0]["data"] == {"x": 10, "z": 20}
    assert events[1]["type"] == "battle.started"

    # Query since seq1
    events_after = store.get_events(since_seq=1)
    assert len(events_after) == 1
    assert events_after[0]["seq"] == 2

    # Query by type
    battle_events = store.get_events(event_type="battle.started")
    assert len(battle_events) == 1
    assert battle_events[0]["type"] == "battle.started"


def test_command_lifecycle():
    store = SessionStore(":memory:")
    cid = store.record_command("cmd_123", "navigation.goto", {"target_x": 15, "target_z": 30})
    assert cid == "cmd_123"

    cmd = store.get_command("cmd_123")
    assert cmd["status"] == "queued"
    assert cmd["parameters"] == {"target_x": 15, "target_z": 30}

    store.update_command("cmd_123", "running")
    assert store.get_command("cmd_123")["status"] == "running"

    store.update_command("cmd_123", "completed", result={"arrived": True})
    cmd_done = store.get_command("cmd_123")
    assert cmd_done["status"] == "completed"
    assert cmd_done["result"] == {"arrived": True}


def test_snapshots():
    store = SessionStore(":memory:")
    snap_id = store.record_snapshot(
        frame=500,
        zone_id=439,
        position=(10, 0, 25),
        mode="OVERWORLD",
        payload={"party_count": 1},
    )
    assert snap_id.startswith("snap_")

    snaps = store.get_snapshots(limit=10)
    assert len(snaps) == 1
    assert snaps[0]["frame"] == 500
    assert snaps[0]["zone_id"] == 439
    assert snaps[0]["player_x"] == 10
    assert snaps[0]["payload"] == {"party_count": 1}


def test_disk_persistence(tmp_path):
    db_file = tmp_path / "test.db"
    store1 = SessionStore(db_file)
    sid = store1.start_session(rom_code="IREJ")
    store1.record_event({"type": "test.event", "summary": "Persisted"})
    store1.close()

    # Reopen
    store2 = SessionStore(db_file)
    events = store2.get_events()
    assert len(events) == 1
    assert events[0]["type"] == "test.event"
    store2.close()
