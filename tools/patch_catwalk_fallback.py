with open("backend/black2/world/navigation_planning.py", "r", encoding="utf-8") as f:
    text = f.read()

old_call_finder = """                        try:
                            static_result = finder(
                                start, goal, player_sample=static_player_sample, occupied=normalized_occupied,
                                allowed=normalized_allowed, constraint_evaluator=constraint_evaluator,
                                movement_mode=effective_movement_mode,
                                allow_unverified_terrain=allow_unverified_terrain,
                            )
                        except TypeError:"""

new_call_finder = """                        try:
                            static_result = finder(
                                start, goal, player_sample=static_player_sample, occupied=normalized_occupied,
                                allowed=normalized_allowed, constraint_evaluator=constraint_evaluator,
                                movement_mode=effective_movement_mode,
                                allow_unverified_terrain=allow_unverified_terrain,
                            )
                            # 若 auto 模式下优先尝试的自行车因独木桥/窄道地形阻断失败，自动回退尝试奔跑/步行
                            if not static_result.get("reachable") and movement_mode == "auto" and effective_movement_mode == "bike":
                                for fb_mode in ("run", "walk"):
                                    fb_res = finder(
                                        start, goal, player_sample=static_player_sample, occupied=normalized_occupied,
                                        allowed=normalized_allowed, constraint_evaluator=constraint_evaluator,
                                        movement_mode=fb_mode,
                                        allow_unverified_terrain=allow_unverified_terrain,
                                    )
                                    if fb_res.get("reachable"):
                                        static_result = fb_res
                                        effective_movement_mode = fb_mode
                                        break
                        except TypeError:"""

text = text.replace(old_call_finder, new_call_finder)

with open("backend/black2/world/navigation_planning.py", "w", encoding="utf-8") as f:
    f.write(text)

print("navigation_planning.py patched with automatic mode fallback from bike to run/walk for catwalks!")
