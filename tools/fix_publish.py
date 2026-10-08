with open("backend/black2/api/navigation_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

old_publish = """    from ..runtime.events import agent_event_bus
    agent_event_bus.publish({
        "type": "navigation.fast_travel.fly_dispatched",
        "departure_zone": cur_zone,
        "destination_zone": body.destination_zone,
        "destination_name": dest.get("name"),
        "landing_grid": grid,
        "fly_pokemon": fly_pokemon,
    })"""

new_publish = """    try:
        from ..runtime.events import agent_event_bus
        await agent_event_bus.publish(
            "navigation.fast_travel.fly_dispatched",
            summary=f"Fast travel flight dispatched from Zone {cur_zone} to Zone {body.destination_zone} ({dest.get('name')}).",
            data={
                "departure_zone": cur_zone,
                "destination_zone": body.destination_zone,
                "destination_name": dest.get("name"),
                "landing_grid": grid,
                "fly_pokemon": fly_pokemon,
            },
        )
    except Exception:
        pass"""

assert old_publish in text
text = text.replace(old_publish, new_publish)
with open("backend/black2/api/navigation_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Fixed publish call in navigation_routes.py!")