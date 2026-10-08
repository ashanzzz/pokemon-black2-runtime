with open("backend/black2/api/navigation_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

old_avail = """@router.get("/fast-travel/available")
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

new_avail = """@router.get("/fast-travel/available")
async def navigation_fast_travel_available(
    category: str | None = Query(None),
    search: str | None = Query(None),
    has_pokemon_center: bool | None = Query(None),
    has_gym: bool | None = Query(None),
) -> Any:
    \"\"\"Return all currently flyable/unlocked destinations.\"\"\"
    cat_val = category if isinstance(category, str) else None
    search_val = search if isinstance(search, str) else None
    pc_val = has_pokemon_center if isinstance(has_pokemon_center, bool) else None
    gym_val = has_gym if isinstance(has_gym, bool) else None
    return fast_travel_service.get_destinations(
        category=cat_val,
        search=search_val,
        status="flyable",
        has_pokemon_center=pc_val,
        has_gym=gym_val,
    )


@router.get("/fast-travel/locked")
async def navigation_fast_travel_locked(
    category: str | None = Query(None),
    search: str | None = Query(None),
    has_pokemon_center: bool | None = Query(None),
    has_gym: bool | None = Query(None),
) -> Any:
    \"\"\"Return all currently locked/unvisited destinations with gating reasons.\"\"\"
    cat_val = category if isinstance(category, str) else None
    search_val = search if isinstance(search, str) else None
    pc_val = has_pokemon_center if isinstance(has_pokemon_center, bool) else None
    gym_val = has_gym if isinstance(has_gym, bool) else None
    return fast_travel_service.get_destinations(
        category=cat_val,
        search=search_val,
        status="locked",
        has_pokemon_center=pc_val,
        has_gym=gym_val,
    )"""

assert old_avail in text
text = text.replace(old_avail, new_avail)
with open("backend/black2/api/navigation_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated navigation_routes.py with direct get_destinations calls!")