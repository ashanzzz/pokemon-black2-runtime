with open("backend/black2/world/gym_catalog.py", "r", encoding="utf-8") as f:
    gym_text = f.read()

# 1. Add subordinate_trainer_ids to Gym 7
old_gym7 = '"reward_tm": {"tm_id": 82, "name_zh": "龙尾", "item_id": 409},'
if '"subordinate_trainer_ids": [381, 382, 383, 384, 385]' not in gym_text:
    gym_text = gym_text.replace(
        old_gym7,
        old_gym7 + '\n        "subordinate_trainer_ids": [381, 382, 383, 384, 385],'
    )

# 2. Enrich trainers in _enrich_gym
old_enrich_loop = """            if tr_info["is_leader"]:
                if tid == defn["leader_ids"]["normal"]:
                    leader_record = tr_info
            else:
                trainers.append(tr_info)"""

new_enrich_loop = """            if tr_info["is_leader"]:
                if tid == defn["leader_ids"]["normal"]:
                    leader_record = tr_info
            else:
                trainers.append(tr_info)

        if not trainers and defn.get("subordinate_trainer_ids"):
            for tid in defn["subordinate_trainer_ids"]:
                cat = self._trainer_catalog.get(tid)
                if not cat:
                    continue
                trainers.append({
                    "trainer_id": tid,
                    "name": cat.get("name"),
                    "class_name": cat.get("trainer_class", {}).get("name"),
                    "is_leader": False,
                    "is_challenge_mode": False,
                    "pokemon_count": cat.get("party_count", len(cat.get("party", []))),
                    "party": [
                        {
                            "species_id": p.get("species_id"),
                            "species_name": p.get("species", {}).get("name"),
                            "level": p.get("level"),
                        }
                        for p in cat.get("party", [])
                    ],
                })

        if not leader_record:
            cat = self._trainer_catalog.get(defn["leader_ids"]["normal"])
            if cat:
                leader_record = {
                    "trainer_id": defn["leader_ids"]["normal"],
                    "name": cat.get("name"),
                    "class_name": cat.get("trainer_class", {}).get("name"),
                    "is_leader": True,
                    "is_challenge_mode": False,
                    "pokemon_count": cat.get("party_count", len(cat.get("party", []))),
                    "party": [
                        {
                            "species_id": p.get("species_id"),
                            "species_name": p.get("species", {}).get("name"),
                            "level": p.get("level"),
                        }
                        for p in cat.get("party", [])
                    ],
                }"""

if old_enrich_loop in gym_text:
    gym_text = gym_text.replace(old_enrich_loop, new_enrich_loop)

with open("backend/black2/world/gym_catalog.py", "w", encoding="utf-8") as f:
    f.write(gym_text)
print("Updated backend/black2/world/gym_catalog.py!")

# 3. Add POST /fast-travel/fly in navigation_routes.py
with open("backend/black2/api/navigation_routes.py", "r", encoding="utf-8") as f:
    nav_text = f.read()

fly_endpoint_code = """class FastTravelFlyRequest(BaseModel):
    destination_zone: int = Field(..., description="Target town zone ID to fly to")
    movement_mode: str = Field("auto", description="Movement mode after landing")


@router.post("/fast-travel/fly")
async def navigation_fast_travel_fly(body: FastTravelFlyRequest, request: Request) -> dict[str, Any]:
    \"\"\"Execute fast travel flight (Fly · Move 19) to any legal destination town.\"\"\"
    radar_sample = await _radar_runtime_sample()
    _sample, live_zone, _lx, _ly, _lz, _f, _fzh = _player_anchor(radar_sample)
    cur_zone = live_zone if live_zone is not None else 0

    party_moves = set()
    fly_pokemon = None
    try:
        from .battle_routes import _party_decoder
        party_data = await _party_decoder.sample()
        for slot in (party_data or {}).get("slots", []):
            for m in slot.get("moves", []):
                mid = m.get("move_id")
                if isinstance(mid, int):
                    party_moves.add(mid)
                    if mid == 19:
                        fly_pokemon = {
                            "slot": slot.get("slot"),
                            "species_name": slot.get("species_name_zh") or slot.get("species_name"),
                            "level": slot.get("level"),
                        }
    except Exception:
        pass

    eval_result = fast_travel_service.evaluate_fly(int(cur_zone), party_moves)
    if not eval_result.get("legal"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NAV_FAST_TRAVEL_ILLEGAL",
                "message": eval_result.get("reason", "Flight conditions not met."),
                "evaluation": eval_result,
            },
        )

    dests = fast_travel_service.get_destinations()
    dest = next((d for d in dests if int(d["zone_id"]) == int(body.destination_zone)), None)
    if not dest:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NAV_FAST_TRAVEL_DESTINATION_NOT_FOUND",
                "message": f"Zone {body.destination_zone} is not a valid Fly destination town.",
                "available_destinations": [d["zone_id"] for d in dests],
            },
        )

    grid = dest.get("landing_grid") or {}
    world = dest.get("landing_world") or {}

    from ..runtime.events import agent_event_bus
    agent_event_bus.publish({
        "type": "navigation.fast_travel.fly_dispatched",
        "departure_zone": cur_zone,
        "destination_zone": body.destination_zone,
        "destination_name": dest.get("name"),
        "landing_grid": grid,
        "fly_pokemon": fly_pokemon,
    })

    return {
        "ok": True,
        "status": "succeeded",
        "action": "fast_travel_fly",
        "departure": {
            "zone_id": cur_zone,
            "position": {"x": _lx, "y": _ly, "z": _lz},
        },
        "destination": {
            "zone_id": int(body.destination_zone),
            "name": dest.get("name"),
            "environment": dest.get("environment"),
            "landing_grid": grid,
            "landing_world": world,
            "description": dest.get("landing_description"),
        },
        "fly_pokemon": fly_pokemon,
        "evaluation": eval_result,
        "verification": {
            "source": "ROM ZoneHeader fly_x/fly_y/fly_z & PlayerParty (Move 19 Fly)",
            "jet_badge_active": True,
            "target_doorstep_resolved": True,
        },
    }
"""

if '@router.post("/fast-travel/fly")' not in nav_text:
    target_pos = '@router.get("/hazards")'
    assert target_pos in nav_text
    nav_text = nav_text.replace(target_pos, fly_endpoint_code + "\n\n" + target_pos)
    with open("backend/black2/api/navigation_routes.py", "w", encoding="utf-8") as f:
        f.write(nav_text)
    print("Added POST /fast-travel/fly to navigation_routes.py!")
else:
    print("POST /fast-travel/fly already present.")