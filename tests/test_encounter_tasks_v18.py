import asyncio

from backend.black2.world.encounter_tasks import EncounterTaskService


class FakeNavigation:
    def __init__(self, snapshot):
        self.control_sample = lambda: snapshot


def make_service(snapshot):
    return EncounterTaskService(
        regions=None,
        planner=None,
        navigation=FakeNavigation(snapshot),
        player_sample=lambda: None,
        poll_seconds=0.001,
        interruption_grace_seconds=0.01,
    )


def test_late_battle_modal_overrides_a_stale_nav_stuck_result():
    service = make_service({
        "semantic": {"context": {"screen_type": "BATTLE", "is_dialogue_active": False}},
        "current": {"layers": [{"id": "battle", "active": True}]},
    })
    status = {"status": "failed", "stop_reason": {"code": "NAV_STUCK"}}

    signal = asyncio.run(service._wait_for_interruption(status))

    assert signal == {
        "kind": "battle",
        "reason": "battle_started",
        "screen_type": "BATTLE",
        "source": "encounter_control_snapshot",
    }
    assert service._interruption(status, signal) is True


def test_dialogue_modal_is_also_a_safe_encounter_stop():
    service = make_service({
        "semantic": {"context": {"screen_type": "DIALOGUE_ACTIVE", "is_dialogue_active": True}},
        "current": {"layers": []},
    })

    signal = service._control_interruption()

    assert signal["kind"] == "dialogue"
    assert signal["reason"] == "dialogue_started"
