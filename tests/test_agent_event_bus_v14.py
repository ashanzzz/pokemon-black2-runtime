import asyncio

from backend.black2.runtime.events import AgentEventBus
from backend.black2.runtime.session_store import SessionStore


def test_event_bus_monotonic_cursor_and_expiry():
    async def scenario():
        bus = AgentEventBus(max_events=2)
        await bus.publish("one", summary="one")
        await bus.publish("two", summary="two")
        await bus.publish("three", summary="three")
        expired = bus.read_since(0)
        assert expired["status"] == "cursor_expired"
        current = bus.read_since(1)
        assert [item["type"] for item in current["events"]] == ["two", "three"]
        assert current["next_cursor"] == 3
    asyncio.run(scenario())


def test_event_bus_wait_timeout_has_fixed_json_contract():
    async def scenario():
        bus = AgentEventBus()
        payload = await bus.wait_since_checked(0, timeout=0.001)
        assert payload == {"status": "ok", "events": [], "next_cursor": 0, "timed_out": True}
    asyncio.run(scenario())


def test_event_bus_wait_wakes_for_new_event():
    async def scenario():
        bus = AgentEventBus()
        waiter = asyncio.create_task(bus.wait_since_checked(0, timeout=1.0))
        await asyncio.sleep(0)
        await bus.publish("runtime.primary_mode.changed", summary="mode")
        payload = await waiter
        assert payload["events"][0]["seq"] == 1
    asyncio.run(scenario())


def test_event_bus_persists_to_session_store():
    async def scenario():
        store = SessionStore(":memory:")
        bus = AgentEventBus(session_store=store)
        await bus.publish("player.moved", summary="Player moved", data={"x": 5, "z": 8})
        await bus.publish("battle.started", summary="Wild battle", data={"species": 10})

        history = bus.query_history(since_seq=0)
        assert len(history) == 2
        assert history[0]["type"] == "player.moved"
        assert history[0]["data"] == {"x": 5, "z": 8}
        assert history[1]["type"] == "battle.started"

        stats = store.get_stats()
        assert stats["events"] == 2
    asyncio.run(scenario())
