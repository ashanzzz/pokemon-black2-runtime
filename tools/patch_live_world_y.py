with open('backend/black2/api/navigation_routes.py', 'r', encoding='utf-8') as f:
    text = f.read()

old_block = """    # Major functional floors have real flat walkable road tiles (>= 3)
    major_floors = sorted([gy for gy, cnt in walkable_flat.items() if cnt >= 3], reverse=True)
    if not major_floors:
        major_floors = [py]
    # General staircase corridor clustering and transition layer flattening
    player_on_stair = False
    stair_runtime = None
    probe_x = px if x is not None else live_x
    probe_z = pz if z is not None else live_z
    if probe_x is not None and probe_z is not None:
        step_eval = staircase_corridor_service.evaluate_player_step(
            resolved_zone, int(probe_x), int(probe_z), live_world_y
        )
        if step_eval:
            player_on_stair = True
            stair_runtime = {
                "active": True,
                **step_eval,
                "stair_slice_y": step_eval["flattened_slice_y"],
                "description": step_eval["ai_guidance"],
            }

    if y is None and player_on_stair and stair_runtime is not None:
        py = stair_runtime["stair_slice_y"]

    all_active_floors = list(major_floors)
    if player_on_stair and stair_runtime is not None and stair_runtime["stair_slice_y"] not in all_active_floors:
        all_active_floors.append(stair_runtime["stair_slice_y"])
    elif py not in all_active_floors:
        all_active_floors.append(py)
    all_active_floors.sort(reverse=True)

    sorted_layers = all_active_floors

    # canonical_grid_player intentionally keeps only grid coordinates; use the
    # richer live PlayerRuntime sample for the true floating-point world height.
    rich_player = player_runtime_service.latest if isinstance(player_runtime_service.latest, dict) else {}
    catwalk_runtime = _catwalk_runtime_info(rich_player)
    slices_cache_key = (
        id(provider), "slices", mode, int(radius), bool(text_map), int(resolved_zone),
        int(px), int(py), int(pz), int(min_x), int(max_x), int(min_z), int(max_z),
        tuple(sorted_layers),
    )
    # Do not cache an active catwalk frame: its dwell timer and balance/fall
    # status must remain live while the player is on the narrow bridge.
    cached_slices = None if catwalk_runtime.get("active") else _radar_cache_get(slices_cache_key)
    if cached_slices is not None:
        cached_kind, cached_payload = cached_slices
        if cached_kind == "text":
            return PlainTextResponse(cached_payload)
        return cached_payload

    live_world_y = float(py) * 16.0
    pos_meta = rich_player.get("position") if isinstance(rich_player.get("position"), dict) else {}
    w_meta = pos_meta.get("world") if isinstance(pos_meta.get("world"), dict) else {}
    if w_meta.get("y") is not None:
        live_world_y = float(w_meta["y"])
    else:
        pos_meta = sample.get("position") if isinstance(sample.get("position"), dict) else {}
        w_meta = pos_meta.get("world") if isinstance(pos_meta.get("world"), dict) else {}
        if w_meta.get("y") is not None:
            live_world_y = float(w_meta["y"])"""

new_block = """    # Major functional floors have real flat walkable road tiles (>= 3)
    major_floors = sorted([gy for gy, cnt in walkable_flat.items() if cnt >= 3], reverse=True)
    if not major_floors:
        major_floors = [py]

    rich_player = player_runtime_service.latest if isinstance(player_runtime_service.latest, dict) else {}
    catwalk_runtime = _catwalk_runtime_info(rich_player)

    live_world_y = float(py) * 16.0
    pos_meta = rich_player.get("position") if isinstance(rich_player.get("position"), dict) else {}
    w_meta = pos_meta.get("world") if isinstance(pos_meta.get("world"), dict) else {}
    if w_meta.get("y") is not None:
        live_world_y = float(w_meta["y"])
    else:
        pos_meta = sample.get("position") if isinstance(sample.get("position"), dict) else {}
        w_meta = pos_meta.get("world") if isinstance(pos_meta.get("world"), dict) else {}
        if w_meta.get("y") is not None:
            live_world_y = float(w_meta["y"])

    # General staircase corridor clustering and transition layer flattening
    player_on_stair = False
    stair_runtime = None
    probe_x = px if x is not None else live_x
    probe_z = pz if z is not None else live_z
    if probe_x is not None and probe_z is not None:
        step_eval = staircase_corridor_service.evaluate_player_step(
            resolved_zone, int(probe_x), int(probe_z), live_world_y
        )
        if step_eval:
            player_on_stair = True
            stair_runtime = {
                "active": True,
                **step_eval,
                "stair_slice_y": step_eval["flattened_slice_y"],
                "description": step_eval["ai_guidance"],
            }

    if y is None and player_on_stair and stair_runtime is not None:
        py = stair_runtime["stair_slice_y"]

    all_active_floors = list(major_floors)
    if player_on_stair and stair_runtime is not None and stair_runtime["stair_slice_y"] not in all_active_floors:
        all_active_floors.append(stair_runtime["stair_slice_y"])
    elif py not in all_active_floors:
        all_active_floors.append(py)
    all_active_floors.sort(reverse=True)

    sorted_layers = all_active_floors

    slices_cache_key = (
        id(provider), "slices", mode, int(radius), bool(text_map), int(resolved_zone),
        int(px), int(py), int(pz), int(min_x), int(max_x), int(min_z), int(max_z),
        tuple(sorted_layers),
    )
    # Do not cache an active catwalk frame: its dwell timer and balance/fall
    # status must remain live while the player is on the narrow bridge.
    cached_slices = None if catwalk_runtime.get("active") else _radar_cache_get(slices_cache_key)
    if cached_slices is not None:
        cached_kind, cached_payload = cached_slices
        if cached_kind == "text":
            return PlainTextResponse(cached_payload)
        return cached_payload"""

assert old_block in text, "old_block not found"
text = text.replace(old_block, new_block, 1)

with open('backend/black2/api/navigation_routes.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Patch applied to order live_world_y before step_eval!")
