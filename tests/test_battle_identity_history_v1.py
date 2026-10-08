from backend.black2.runtime.battle_identity_history import BattleIdentityHistory


def _identity(species_id=509):
    return {
        "format": "black2-battle-identity/v1",
        "status": "candidate",
        "verified": False,
        "battle_kind": {"status": "candidate", "value": "wild", "confidence": 0.60},
        "trainer": {"status": "unresolved", "trainer_id": None, "name": None, "class": None},
        "player": {
            "status": "candidate",
            "party": [{"species_id": 501, "species": {"names": {"zh-Hans": "水水獭"}}}],
        },
        "opponent": {
            "status": "candidate",
            "party": [{"species_id": species_id, "species": {"names": {"zh-Hans": "扒手猫"}}}],
        },
        "reason": "RAM species candidate",
        "limitations": ["trainer causality unresolved"],
    }


def test_history_keeps_identity_after_battle_ends(tmp_path):
    store = BattleIdentityHistory(tmp_path / "identity.ndjson")
    first = store.record(
        _identity(),
        presence={"active": True, "active_status": "candidate", "frame": 1234},
        context={"zone_id": 437, "battle_overworld": {"zone_id": 437}},
        session_id="s1",
    )
    duplicate = store.record(
        _identity(),
        presence={"active": True, "frame": 1240},
        context={"zone_id": 437},
        session_id="s1",
    )

    assert first["recorded"] is True
    assert duplicate["deduplicated"] is True
    recent = store.recent(1)
    assert recent["count"] == 1
    saved = recent["observations"][0]
    assert saved["zone_id"] == 437
    assert saved["identity"]["opponent"]["party"][0]["species_id"] == 509
    assert saved["identity"]["opponent"]["party"][0]["species"]["names"]["zh-Hans"] == "扒手猫"


def test_history_does_not_merge_different_encounters(tmp_path):
    store = BattleIdentityHistory(tmp_path / "identity.ndjson")
    for frame, species_id in ((1, 509), (2, 504)):
        result = store.record(
            _identity(species_id),
            presence={"active": True, "frame": frame},
            context={"zone_id": 437},
            session_id="s1",
        )
        assert result["recorded"] is True

    recent = store.recent(10)
    assert recent["total_count"] == 2
    assert [row["identity"]["opponent"]["party"][0]["species_id"] for row in recent["observations"]] == [509, 504]
