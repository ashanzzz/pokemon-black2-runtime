# 1. Update navigation_routes.py
with open("backend/black2/api/navigation_routes.py", "r", encoding="utf-8") as f:
    nav_text = f.read()

old_dest = """@router.get("/fast-travel/destinations")
async def navigation_fast_travel_destinations() -> list[dict[str, Any]]:
    \"\"\"Return all official ROM town destinations with fly coordinates.\"\"\"
    return fast_travel_service.get_destinations()"""

new_dest = """@router.get("/fast-travel/destinations")
async def navigation_fast_travel_destinations(
    category: str | None = Query(None, description="Filter category: 'gym_cities', 'cities', 'towns', 'landmarks', 'routes', 'all'"),
    search: str | None = Query(None, description="Fuzzy search by name or zone_id"),
    has_pokemon_center: bool | None = Query(None, description="Filter by presence of Pokemon Center"),
    has_gym: bool | None = Query(None, description="Filter by presence of Gym"),
    format: str = Query("detailed", description="'detailed' (returns envelope with flight status), 'flat' (array)"),
) -> Any:
    \"\"\"Return all official ROM town destinations with fly coordinates and metadata.\"\"\"
    if format == "flat":
        return fast_travel_service.get_destinations(
            category=category,
            search=search,
            has_pokemon_center=has_pokemon_center,
            has_gym=has_gym,
        )

    radar_sample = await _radar_runtime_sample()
    _sample, live_zone, _lx, _ly, _lz, _f, _fzh = _player_anchor(radar_sample)
    party_moves = set()
    fly_mount = None
    try:
        from .battle_routes import _party_decoder
        party_data = await _party_decoder.sample()
        for slot in (party_data or {}).get("slots", []):
            for m in slot.get("moves", []):
                mid = m.get("move_id")
                if isinstance(mid, int):
                    party_moves.add(mid)
                    if mid == 19:
                        fly_mount = {
                            "slot": slot.get("slot"),
                            "species_name": slot.get("species_name_zh") or slot.get("species_name"),
                            "species_id": slot.get("species"),
                            "level": slot.get("level"),
                            "move_id": 19,
                            "move_name": m.get("name_zh") or m.get("name") or "飞翔",
                        }
    except Exception:
        pass

    curr_grid = {"x": _lx, "y": _ly, "z": _lz} if _lx is not None else None
    return fast_travel_service.get_flyable_regions_catalog(
        category=category,
        search=search,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        current_zone_id=live_zone,
        current_grid=curr_grid,
        party_moves=party_moves,
        flying_mount=fly_mount,
    )


@router.get("/fast-travel/regions")
async def navigation_fast_travel_regions(
    category: str | None = Query(None),
    search: str | None = Query(None),
    has_pokemon_center: bool | None = Query(None),
    has_gym: bool | None = Query(None),
) -> Any:
    \"\"\"Read all flight-accessible regions/locations with full metadata and flight status.\"\"\"
    return await navigation_fast_travel_destinations(
        category=category,
        search=search,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        format="detailed",
    )"""

assert old_dest in nav_text
nav_text = nav_text.replace(old_dest, new_dest)
with open("backend/black2/api/navigation_routes.py", "w", encoding="utf-8") as f:
    f.write(nav_text)
print("Updated navigation_routes.py with rich fly destinations and regions!")

# 2. Update app.py
with open("backend/black2/api/app.py", "r", encoding="utf-8") as f:
    app_text = f.read()

old_app_dest = """@app.get("/api/v1/player/fly/destinations")
async def global_player_fly_destinations():
    return await navigation_fast_travel_destinations()"""

new_app_dest = """@app.get("/api/v1/player/fly/destinations")
@app.get("/api/v1/player/fly/regions")
async def global_player_fly_destinations(
    category: Optional[str] = None,
    search: Optional[str] = None,
    has_pokemon_center: Optional[bool] = None,
    has_gym: Optional[bool] = None,
    format: str = "detailed",
):
    return await navigation_fast_travel_destinations(
        category=category,
        search=search,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        format=format,
    )"""

if old_app_dest in app_text:
    app_text = app_text.replace(old_app_dest, new_app_dest)
    with open("backend/black2/api/app.py", "w", encoding="utf-8") as f:
        f.write(app_text)
    print("Updated app.py with /api/v1/player/fly/regions and query parameters!")
else:
    print("old_app_dest not found in app.py")