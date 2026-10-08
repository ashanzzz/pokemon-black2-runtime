from __future__ import annotations

import asyncio

import pytest

from backend.black2.actions.input_lease import InputLease
from backend.black2.actions.prepared_actions import PreparedActionService
from backend.black2.runtime.events import agent_event_bus


def _state(session: str = "session-a", frame: int = 100, request: str | None = "req-1") -> dict:
    return {
        "transport": {"session_id": session, "frame": frame},
        "semantic": {"context": {"screen_type": "BATTLE"}},
        "battle": {"request_id": request},
    }


async def _wait_for(service: PreparedActionService, action_id: str, status: str) -> dict:
    for _ in range(100):
        result = service.get(action_id)
        if result["status"] == status:
            return result
        await asyncio.sleep(0.005)
    return service.get(action_id)


def test_action_waits_for_named_event_before_execution() -> None:
    async def scenario() -> None:
        state = _state()
        executed: list[dict] = []

        async def sink(event_type: str, **_kwargs):
            return None

        service = PreparedActionService(
            state_provider=lambda: state,
            executor=lambda command: executed.append(command) or {"ok": True},
            event_sink=sink,
            poll_seconds=0.001,
        )
        record = await service.submit(
            kind="battle_command",
            command={"type": "use_move", "move_slot": 1},
            preconditions={"session_id": "session-a", "primary_mode": "BATTLE", "request_id": "req-1"},
            execute_when={"event": "battle.request.ready"},
        )
        await asyncio.sleep(0.01)
        assert service.get(record["action_id"])["status"] == "waiting_precondition"
        await agent_event_bus.publish("battle.request.ready", summary="request ready")
        result = await _wait_for(service, record["action_id"], "completed")
        assert result["status"] == "completed"
        assert executed == [{"type": "use_move", "move_slot": 1}]

    asyncio.run(scenario())


def test_session_change_marks_action_stale() -> None:
    async def scenario() -> None:
        state = _state()
        service = PreparedActionService(
            state_provider=lambda: state,
            executor=lambda _command: {"unexpected": True},
            poll_seconds=0.001,
        )
        record = await service.submit(
            kind="dialogue_choice",
            command={"type": "select_choice", "choice_index": 0},
            preconditions={"session_id": "session-a", "primary_mode": "BATTLE", "request_id": "req-1"},
        )
        state["transport"]["session_id"] = "session-b"
        result = await _wait_for(service, record["action_id"], "stale")
        assert result["failure"]["reason"] == "session_id_changed"

    asyncio.run(scenario())


def test_request_change_marks_battle_action_stale() -> None:
    async def scenario() -> None:
        state = _state()
        executed: list[dict] = []
        service = PreparedActionService(
            state_provider=lambda: state,
            executor=lambda command: executed.append(command) or {"ok": True},
            poll_seconds=0.001,
        )
        record = await service.submit(
            kind="battle_command",
            command={"type": "use_move", "move_slot": 1},
            preconditions={"session_id": "session-a", "primary_mode": "BATTLE", "request_id": "req-1"},
        )
        state["battle"]["request_id"] = "req-2"
        result = await _wait_for(service, record["action_id"], "stale")
        assert result["failure"]["reason"] == "request_id_changed"
        assert executed == []

    asyncio.run(scenario())


def test_expired_action_never_calls_executor() -> None:
    async def scenario() -> None:
        state = _state(frame=10)
        executed: list[dict] = []
        service = PreparedActionService(
            state_provider=lambda: state,
            executor=lambda command: executed.append(command) or {"ok": True},
            poll_seconds=0.001,
        )
        record = await service.submit(
            kind="battle_command",
            command={"type": "use_move", "move_slot": 1},
            preconditions={"session_id": "session-a", "primary_mode": "BATTLE", "request_id": "req-1"},
            ttl_frames=1,
        )
        state["transport"]["frame"] = 12
        result = await _wait_for(service, record["action_id"], "stale")
        assert result["failure"]["reason"] == "expired"
        assert executed == []

    asyncio.run(scenario())


def test_idempotency_key_returns_same_action() -> None:
    async def scenario() -> None:
        service = PreparedActionService(state_provider=lambda: _state(), executor=lambda _command: {"ok": True})
        first = await service.submit(
            kind="battle_command", command={"type": "use_move", "move_slot": 1},
            preconditions={"session_id": "session-a", "primary_mode": "BATTLE"},
            idempotency_key="turn-1",
        )
        second = await service.submit(
            kind="battle_command", command={"type": "use_move", "move_slot": 1},
            preconditions={"session_id": "session-a", "primary_mode": "BATTLE"},
            idempotency_key="turn-1",
        )
        assert second["action_id"] == first["action_id"]
        assert second["idempotent_replay"] is True

    asyncio.run(scenario())


def test_raw_button_command_is_rejected() -> None:
    async def scenario() -> None:
        service = PreparedActionService(state_provider=lambda: _state(), executor=lambda _command: {"ok": True})
        with pytest.raises(ValueError, match="raw button"):
            await service.submit(kind="raw", command={"buttons": ["A"]})

    asyncio.run(scenario())


def test_two_services_share_one_input_lease() -> None:
    async def scenario() -> None:
        lease = InputLease()
        active = 0
        maximum = 0

        async def executor(_command: dict) -> dict:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"ok": True}

        services = [
            PreparedActionService(state_provider=lambda: _state(), executor=executor, lease=lease, poll_seconds=0.001)
            for _ in range(2)
        ]
        records = [
            await service.submit(
                kind="dialogue_choice",
                command={"type": "select_choice", "choice_index": i},
                preconditions={"session_id": "session-a", "primary_mode": "BATTLE"},
            )
            for i, service in enumerate(services)
        ]
        results = [await _wait_for(service, record["action_id"], "completed") for service, record in zip(services, records)]
        assert all(result["status"] == "completed" for result in results)
        assert maximum == 1

    asyncio.run(scenario())
