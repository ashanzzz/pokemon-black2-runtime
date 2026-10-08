with open("backend/black2/api/navigation_routes.py", "r", encoding="utf-8") as f:
    nav_text = f.read()

old_fn = """@router.get("/fast-travel/destinations")
async def navigation_fast_travel_destinations(
    category: str | None = Query(None, description="Filter category: 'gym_cities', 'cities', 'towns', 'landmarks', 'routes', 'all'"),
    search: str | None = Query(None, description="Fuzzy search by name or zone_id"),
    has_pokemon_center: bool | None = Query(None, description="Filter by presence of Pokemon Center"),
    has_gym: bool | None = Query(None, description="Filter by presence of Gym"),
    format: str = Query("flat", description="'flat' (returns list of destinations), 'detailed' (returns envelope with flight status)"),
) -> Any:"""

new_fn = """@router.get("/fast-travel/destinations")
async def navigation_fast_travel_destinations(
    category: str | None = Query(None, description="Filter category: 'gym_cities', 'cities', 'towns', 'landmarks', 'routes', 'all'"),
    search: str | None = Query(None, description="Fuzzy search by name or zone_id"),
    status: str | None = Query(None, description="Filter status: 'flyable'/'available' (open towns), 'locked' (unvisited/gated towns), 'all'"),
    flyable: bool | None = Query(None, description="Filter by exact flyable boolean"),
    has_pokemon_center: bool | None = Query(None, description="Filter by presence of Pokemon Center"),
    has_gym: bool | None = Query(None, description="Filter by presence of Gym"),
    format: str = Query("flat", description="'flat' (returns list of destinations), 'detailed' (returns envelope with flight status)"),
) -> Any:"""

old_return_flat = """    if format == "flat":
        return fast_travel_service.get_destinations(
            category=category,
            search=search,
            has_pokemon_center=has_pokemon_center,
            has_gym=has_gym,
        )"""

new_return_flat = """    if format == "flat":
        return fast_travel_service.get_destinations(
            category=category,
            search=search,
            status=status,
            flyable=flyable,
            has_pokemon_center=has_pokemon_center,
            has_gym=has_gym,
        )"""

old_call_catalog = """    return fast_travel_service.get_flyable_regions_catalog(
        category=category,
        search=search,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        current_zone_id=live_zone,
        current_grid=curr_grid,
        party_moves=party_moves,
        flying_mount=fly_mount,
    )"""

new_call_catalog = """    return fast_travel_service.get_flyable_regions_catalog(
        category=category,
        search=search,
        status=status,
        flyable=flyable,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        current_zone_id=live_zone,
        current_grid=curr_grid,
        party_moves=party_moves,
        flying_mount=fly_mount,
    )"""

old_regions_endpoint = """@router.get("/fast-travel/regions")
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

new_regions_endpoint = """@router.get("/fast-travel/regions")
async def navigation_fast_travel_regions(
    category: str | None = Query(None),
    search: str | None = Query(None),
    status: str | None = Query(None),
    flyable: bool | None = Query(None),
    has_pokemon_center: bool | None = Query(None),
    has_gym: bool | None = Query(None),
) -> Any:
    \"\"\"Read all flight-accessible regions/locations with full metadata and flight status.\"\"\"
    return await navigation_fast_travel_destinations(
        category=category,
        search=search,
        status=status,
        flyable=flyable,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        format="detailed",
    )


@router.get("/fast-travel/available")
async def navigation_fast_travel_available(
    category: str | None = Query(None),
    search: str | None = Query(None),
    has_pokemon_center: bool | None = Query(None),
    has_gym: bool | None = Query(None),
) -> Any:
    \"\"\"Return all currently flyable/unlocked destinations.\"\"\"
    return await navigation_fast_travel_destinations(
        category=category,
        search=search,
        status="flyable",
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        format="flat",
    )


@router.get("/fast-travel/locked")
async def navigation_fast_travel_locked(
    category: str | None = Query(None),
    search: str | None = Query(None),
    has_pokemon_center: bool | None = Query(None),
    has_gym: bool | None = Query(None),
) -> Any:
    \"\"\"Return all currently locked/unvisited destinations with gating reasons.\"\"\"
    return await navigation_fast_travel_destinations(
        category=category,
        search=search,
        status="locked",
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        format="flat",
    )"""

assert old_fn in nav_text
assert old_return_flat in nav_text
assert old_call_catalog in nav_text
assert old_regions_endpoint in nav_text

nav_text = nav_text.replace(old_fn, new_fn).replace(old_return_flat, new_return_flat).replace(old_call_catalog, new_call_catalog).replace(old_regions_endpoint, new_regions_endpoint)

with open("backend/black2/api/navigation_routes.py", "w", encoding="utf-8") as f:
    f.write(nav_text)
print("Updated navigation_routes.py with /available and /locked endpoints!")

# Update app.py
with open("backend/black2/api/app.py", "r", encoding="utf-8") as f:
    app_text = f.read()

old_app_dest = """@app.get("/api/v1/player/fly/destinations")
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

new_app_dest = """@app.get("/api/v1/player/fly/destinations")
@app.get("/api/v1/player/fly/regions")
async def global_player_fly_destinations(
    category: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    flyable: Optional[bool] = None,
    has_pokemon_center: Optional[bool] = None,
    has_gym: Optional[bool] = None,
    format: str = "detailed",
):
    return await navigation_fast_travel_destinations(
        category=category,
        search=search,
        status=status,
        flyable=flyable,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        format=format,
    )

@app.get("/api/v1/player/fly/available")
async def global_player_fly_available(
    category: Optional[str] = None,
    search: Optional[str] = None,
    has_pokemon_center: Optional[bool] = None,
    has_gym: Optional[bool] = None,
):
    from .navigation_routes import navigation_fast_travel_available
    return await navigation_fast_travel_available(
        category=category,
        search=search,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
    )

@app.get("/api/v1/player/fly/locked")
async def global_player_fly_locked(
    category: Optional[str] = None,
    search: Optional[str] = None,
    has_pokemon_center: Optional[bool] = None,
    has_gym: Optional[bool] = None,
):
    from .navigation_routes import navigation_fast_travel_locked
    return await navigation_fast_travel_locked(
        category=category,
        search=search,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
    )"""

assert old_app_dest in app_text
app_text = app_text.replace(old_app_dest, new_app_dest)
with open("backend/black2/api/app.py", "w", encoding="utf-8") as f:
    f.write(app_text)
print("Updated app.py with global_player_fly_available and global_player_fly_locked!")