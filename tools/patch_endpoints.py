with open('backend/black2/api/navigation_routes.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Add elevation_transit before return plan
old_return = """        navigation_audit_log.record(
            "plan", "plan_ready", plan_id=plan.get("plan_id"),
            request=body.model_dump(), resolved_destination=destination,
            occupancy_meta=_occupancy_meta, resolved_start=plan.get("resolved_start"),
            resolved_goal=plan.get("resolved_goal"), route_source=plan.get("route_source"),
            confidence=plan.get("confidence"), movement=plan.get("movement"),
            segments=plan.get("segments"), warnings=plan.get("warnings"),
        )
        return plan"""

new_return = """        # Enrich plan with structured elevation_transit for AI observability
        stair_nodes = []
        route_detail = plan.get("route_detail") or {}
        nodes = route_detail.get("nodes") or []
        provider = _planner._resolve_static_provider()
        for idx, n in enumerate(nodes):
            nx, nz, ny = n.get("x"), n.get("z"), n.get("y", 0)
            n_zone = n.get("zone_id", occupancy_zone)
            if provider is not None and nx is not None and nz is not None:
                nsurf = provider.surface_at(int(n_zone), int(nx), int(nz), int(ny), allow_unverified_terrain=True)
                for s in (nsurf or {}).get("surfaces") or []:
                    sh = s.get("height") or {}
                    sl = sh.get("slope_index", 0)
                    if sl > 0:
                        s_rel_y = sh.get("chunk_relative_world_y") or 0.0
                        stair_nodes.append({
                            "node_index": idx,
                            "tile": {"x": nx, "z": nz},
                            "floor_y": ny,
                            "world_y": round(s_rel_y, 2),
                            "slope_index": sl,
                            "role": "lower_step" if s_rel_y < 16.0 else "upper_step",
                        })
                        break

        if stair_nodes:
            plan["elevation_transit"] = {
                "staircase_detected": True,
                "total_stair_steps": len(stair_nodes),
                "steps": stair_nodes,
                "handrails": "north_south_blocked",
                "recommended_movement": "run",
                "ai_guidance": "路径包含立体台阶跨层；南北护栏封死，沿走向直行；推荐跑步，禁止骑行车",
            }
        else:
            plan["elevation_transit"] = {
                "staircase_detected": False,
                "total_stair_steps": 0,
                "steps": [],
                "handrails": "none",
                "recommended_movement": plan.get("movement", {}).get("selected", "run"),
                "ai_guidance": "平整单层地面通道，无跨层阶梯",
            }

        navigation_audit_log.record(
            "plan", "plan_ready", plan_id=plan.get("plan_id"),
            request=body.model_dump(), resolved_destination=destination,
            occupancy_meta=_occupancy_meta, resolved_start=plan.get("resolved_start"),
            resolved_goal=plan.get("resolved_goal"), route_source=plan.get("route_source"),
            confidence=plan.get("confidence"), movement=plan.get("movement"),
            segments=plan.get("segments"), warnings=plan.get("warnings"),
        )
        return plan"""

assert old_return in text, "old_return not found"
text = text.replace(old_return, new_return, 1)

# Add endpoints before @router.get("/hazards")
old_haz = '@router.get("/hazards")'
new_haz = """@router.get("/surf/transitions")
async def navigation_surf_transitions(zone_id: int | None = Query(None)) -> dict[str, Any]:
    \"\"\"Discover legal land-to-water jump points and water-to-land dismount points.\"\"\"
    radar_sample = await _radar_runtime_sample()
    _sample, live_zone, _lx, _ly, _lz, _f, _fzh = _player_anchor(radar_sample)
    target_zone = zone_id if zone_id is not None else (live_zone if live_zone is not None else 448)
    return surf_transition_service.analyze_zone(int(target_zone))


@router.get("/fast-travel/destinations")
async def navigation_fast_travel_destinations() -> list[dict[str, Any]]:
    \"\"\"Return all official ROM town destinations with fly coordinates.\"\"\"
    return fast_travel_service.get_destinations()


@router.get("/fast-travel/evaluate")
async def navigation_fast_travel_evaluate(zone_id: int | None = Query(None)) -> dict[str, Any]:
    \"\"\"Evaluate whether Fly fast travel is legal from the current or queried zone.\"\"\"
    radar_sample = await _radar_runtime_sample()
    _sample, live_zone, _lx, _ly, _lz, _f, _fzh = _player_anchor(radar_sample)
    target_zone = zone_id if zone_id is not None else (live_zone if live_zone is not None else 0)
    party_moves = set()
    try:
        from ..decoders.party_runtime import PlayerPartyDecoder
        party_dec = PlayerPartyDecoder()
        party_data = party_dec.decode()
        for mon in (party_data or {}).get("pokemon", []):
            for m in mon.get("moves", []):
                mid = m.get("id")
                if isinstance(mid, int):
                    party_moves.add(mid)
    except Exception:
        pass
    return fast_travel_service.evaluate_fly(int(target_zone), party_moves)


@router.get("/hazards")"""

assert old_haz in text, "old_haz not found"
text = text.replace(old_haz, new_haz, 1)

with open('backend/black2/api/navigation_routes.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Updated navigation_routes.py with elevation_transit and new endpoints!")
