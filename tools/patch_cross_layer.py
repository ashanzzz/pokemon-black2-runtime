with open("backend/black2/world/static_navigation.py", "r", encoding="utf-8") as f:
    text = f.read()

cross_layer_method = """    def find_cross_layer_path(
        self,
        start: NavNode,
        goal: NavNode,
        *,
        movement_mode: str = "walk",
        player_sample: dict[str, Any] | None = None,
        occupied: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        allowed: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        constraint_evaluator: Any | None = None,
        allow_unverified_terrain: bool = False,
    ) -> dict[str, Any]:
        \"\"\"Find multi-layer path bridging elevation differences via discovered staircase corridors.\"\"\"
        from .staircase_corridors import StaircaseCorridorService
        scs = StaircaseCorridorService(self)
        corridors = scs.analyze_zone(int(start.zone_id))
        candidates = []
        for c in corridors:
            lp, up = c.lower_portal, c.upper_portal
            if not lp or not up:
                continue
            if lp.get("floor_y") == start.y and up.get("floor_y") == goal.y:
                candidates.append((c, lp, up, False))
            elif up.get("floor_y") == start.y and lp.get("floor_y") == goal.y:
                candidates.append((c, up, lp, True))

        if not candidates:
            return {
                "reachable": False,
                "reason": f"No staircase corridor connects layer Y={start.y} to Y={goal.y} in Zone {start.zone_id}",
                "path": [],
                "confidence": "candidate_static",
            }

        best_route = None
        min_total_cost = float("inf")

        for c, entry_p, exit_p, desc in candidates:
            entry_node = NavNode(start.zone_id, entry_p["x"], start.y, entry_p["z"])
            p1 = self.find_path(
                start, entry_node,
                movement_mode=movement_mode, player_sample=player_sample,
                occupied=occupied, allowed=allowed,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )
            if not p1.get("reachable"):
                continue

            exit_node = NavNode(goal.zone_id, exit_p["x"], goal.y, exit_p["z"])
            p3 = self.find_path(
                exit_node, goal,
                movement_mode=movement_mode, player_sample=player_sample,
                occupied=occupied, allowed=allowed,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )
            if not p3.get("reachable"):
                continue

            stair_steps = []
            steps_ordered = list(c.steps)
            if desc:
                steps_ordered.reverse()
            for s in steps_ordered:
                stair_steps.append({
                    "zone_id": int(start.zone_id),
                    "x": int(s.x),
                    "y": int(c.flattened_slice_y),
                    "z": int(s.z),
                })

            p1_path = p1.get("path", [])
            p3_path = p3.get("path", [])
            full_path = list(p1_path) + stair_steps + list(p3_path)
            total_steps = max(0, len(full_path) - 1)

            if total_steps < min_total_cost:
                min_total_cost = total_steps
                best_route = {
                    "reachable": True,
                    "path": full_path,
                    "cost": total_steps,
                    "corridor": c.as_dict(),
                    "staircase_detected": True,
                    "confidence": "candidate_static",
                    "source": "staircase_corridor_stitched",
                }

        if best_route is not None:
            return best_route

        return {
            "reachable": False,
            "reason": "No connected path through available staircase corridors",
            "path": [],
            "confidence": "candidate_static",
        }

"""

# Insert find_cross_layer_path right before find_path
if "def find_cross_layer_path" not in text:
    text = text.replace("    def find_path(", cross_layer_method + "    def find_path(")

# In find_path, replace:
#   if start.y != goal.y:
#       return {"reachable": False, "reason": "static candidate graph does not infer an elevation transition", "path": [], "confidence": "candidate_static"}
old_check = """        if start.y != goal.y:
            return {"reachable": False, "reason": "static candidate graph does not infer an elevation transition", "path": [], "confidence": "candidate_static"}"""

new_check = """        if start.y != goal.y:
            return self.find_cross_layer_path(
                start, goal,
                movement_mode=movement_mode,
                player_sample=player_sample,
                occupied=occupied,
                allowed=allowed,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )"""

text = text.replace(old_check, new_check)

with open("backend/black2/world/static_navigation.py", "w", encoding="utf-8") as f:
    f.write(text)

print("static_navigation.py patched with find_cross_layer_path!")
