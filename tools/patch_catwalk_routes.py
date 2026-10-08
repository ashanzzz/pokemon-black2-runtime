with open('backend/black2/api/navigation_routes.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Add imports
old_imp = "from ..progression.state import progression_state_service, resolve_trainer_defeat_flag, is_event_flag_set"
new_imp = """from ..progression.state import progression_state_service, resolve_trainer_defeat_flag, is_event_flag_set
from ..world.surf_transitions import surf_transition_service
from ..world.fast_travel import fast_travel_service"""

assert old_imp in text, "old_imp not found"
text = text.replace(old_imp, new_imp, 1)

# Add side_drop_landings to catwalk_meta
old_catwalk_end = """        catwalk_meta = {
            "type": "catwalk_entry" if tclass == 0xBF or material.get("kind") == "catwalk_entry" else "catwalk_body",
            "symbol": "╪" if tclass == 0xBF or material.get("kind") == "catwalk_entry" else "╫",
            "axis": catwalk_axis,
            "allowed_exits": allowed_axis,
            "side_drop_directions": side_drop,
            "fall_risk": "high",
            "dwell_monitoring": "required",
            "dwell_limit_status": "unverified_runtime_threshold",
            "status": f"⚠️ 独木桥/窄桥：只能沿桥轴 {allowed_axis} 通行；{side_text}；停留平衡计时需实时 RAM 验证",
        }"""

new_catwalk_end = """        side_drop_landings = []
        if provider is not None:
            dir_map = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
            for drop_dir in side_drop:
                ddx, ddz = dir_map.get(drop_dir, (0, 0))
                lx, lz = int(x) + ddx, int(z) + ddz
                g_probe = provider.surface_at(int(zone_id), lx, lz, 0, allow_unverified_terrain=True)
                for gs in (g_probe or {}).get("surfaces") or []:
                    gmat = gs.get("material") or {}
                    gcol = gs.get("collision") or {}
                    gh = gs.get("height") or {}
                    if not gcol.get("static_blocked") and gmat.get("kind") not in ("obstacle", "water"):
                        gy = gh.get("chunk_relative_world_y") or 0.0
                        side_drop_landings.append({
                            "direction": drop_dir,
                            "landing_tile": {"x": lx, "z": lz, "floor_y": 0, "world_y": gy},
                            "height_drop": round((relative_y or 32.0) - gy, 1),
                            "landing_material": gmat.get("kind", "ground"),
                        })
                        break
        catwalk_meta = {
            "type": "catwalk_entry" if tclass == 0xBF or material.get("kind") == "catwalk_entry" else "catwalk_body",
            "symbol": "╪" if tclass == 0xBF or material.get("kind") == "catwalk_entry" else "╫",
            "axis": catwalk_axis,
            "allowed_exits": allowed_axis,
            "side_drop_directions": side_drop,
            "side_drop_landings": side_drop_landings,
            "fall_risk": "high",
            "dwell_monitoring": "required",
            "dwell_limit_status": "unverified_runtime_threshold",
            "status": f"⚠️ 独木桥/窄桥：主轴沿 {allowed_axis} 通行；{side_text}；侧向可跳下至下层平地 (标高 0.0)；停留平衡计时需实时 RAM 验证",
        }"""

assert old_catwalk_end in text, "old_catwalk_end not found"
text = text.replace(old_catwalk_end, new_catwalk_end, 1)

with open('backend/black2/api/navigation_routes.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Updated navigation_routes.py with imports and catwalk side drop!")
