from backend.black2.state.playtest_memory import PlaytestMemoryStore


def test_memory_sync_keeps_direct_player_location_and_unknown_inventory(tmp_path):
    store = PlaytestMemoryStore(tmp_path)
    state = store.sync_runtime(
        {"profile": {"badges": None}},
        player={
            "status": "resolved",
            "confidence": "probable",
            "zone_id": 439,
            "position": {"grid": {"x": 108, "y": 1, "z": 693}},
        },
        party={"status": "partial", "count": 1, "slots": [{"slot": 1, "level": 6}]},
        inventory={"status": "unresolved", "items": []},
        objective={"title": "find next story event", "status": "active"},
        note="read nearby story candidates",
    )

    current = state["medium_term"]["current_state"]
    assert current["location"] == {
        "zone_id": 439,
        "grid": {"x": 108, "y": 1, "z": 693},
        "confidence": "probable",
    }
    assert current["party"]["count"] == 1
    assert current["inventory"]["status"] == "unresolved"
    assert current["inventory"]["items"] == []


def test_memory_short_term_is_bounded_and_persisted(tmp_path):
    store = PlaytestMemoryStore(tmp_path)
    for index in range(55):
        store.record_event({"kind": "probe", "index": index})

    snapshot = store.snapshot()
    recent = snapshot["short_term"]["recent"]
    assert len(recent) == 50
    assert recent[0]["index"] == 5
    assert recent[-1]["index"] == 54
    assert len(store.short_path.read_text(encoding="utf-8").splitlines()) == 55
