with open("backend/black2/world/static_navigation.py", "r", encoding="utf-8") as f:
    text = f.read()

old_has_candidate = """    def has_candidate_edge(
        self, start: NavNode, goal: NavNode, *, player_sample: dict[str, Any] | None = None,
        occupied: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        movement_mode: str = "walk",
        constraint_evaluator: Any | None = None,
        allow_unverified_terrain: bool = False,
    ) -> bool:
        if abs(start.x - goal.x) + abs(start.z - goal.z) != 1:
            return False
        if start.y == goal.y:
            result = self.find_path(
                start, goal, player_sample=player_sample, occupied=occupied,
                movement_mode=movement_mode,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )
            return bool(result.get("reachable") and len(result.get("path") or []) == 2)
        # 跨层阶梯过渡边核验 (支持下层入口 ⇄ 台阶 ⇄ 上层出口)
        try:
            from .staircase_corridors import StaircaseCorridorService
            scs = StaircaseCorridorService(self)
            for c in scs.analyze_zone(int(start.zone_id)):
                lp, up, steps = c.lower_portal, c.upper_portal, c.steps
                if not lp or not up or not steps:
                    continue
                # 检查是否为下层入口 ⇄ 第1阶
                if ((start.x, start.z) == (lp["x"], lp["z"]) and (goal.x, goal.z) == (steps[0].x, steps[0].z)) or                    ((goal.x, goal.z) == (lp["x"], lp["z"]) and (start.x, start.z) == (steps[0].x, steps[0].z)):
                    return True
                # 检查是否为最后一阶 ⇄ 上层出口
                if ((start.x, start.z) == (steps[-1].x, steps[-1].z) and (goal.x, goal.z) == (up["x"], up["z"])) or                    ((goal.x, goal.z) == (steps[-1].x, steps[-1].z) and (start.x, start.z) == (up["x"], up["z"])):
                    return True
                # 检查台阶之间连续过渡
                for i in range(len(steps) - 1):
                    if ((start.x, start.z) == (steps[i].x, steps[i].z) and (goal.x, goal.z) == (steps[i+1].x, steps[i+1].z)) or                        ((goal.x, goal.z) == (steps[i].x, steps[i].z) and (start.x, start.z) == (steps[i+1].x, steps[i+1].z)):
                        return True
        except Exception:
            pass
        return False"""

new_has_candidate = """    def has_candidate_edge(
        self, start: NavNode, goal: NavNode, *, player_sample: dict[str, Any] | None = None,
        occupied: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        movement_mode: str = "walk",
        constraint_evaluator: Any | None = None,
        allow_unverified_terrain: bool = False,
    ) -> bool:
        if abs(start.x - goal.x) + abs(start.z - goal.z) != 1:
            return False
        # 1. 优先校验阶梯过渡边 (无论 start.y 与 goal.y 是否相等，只要属于阶梯过渡边即合法)
        try:
            from .staircase_corridors import StaircaseCorridorService
            scs = StaircaseCorridorService(self)
            for c in scs.analyze_zone(int(start.zone_id)):
                lp, up, steps = c.lower_portal, c.upper_portal, c.steps
                if not lp or not up or not steps:
                    continue
                # 检查下层入口 ⇄ 第1阶
                if ((start.x, start.z) == (lp["x"], lp["z"]) and (goal.x, goal.z) == (steps[0].x, steps[0].z)) or \
                   ((goal.x, goal.z) == (lp["x"], lp["z"]) and (start.x, start.z) == (steps[0].x, steps[0].z)):
                    return True
                # 检查最后一阶 ⇄ 上层出口
                if ((start.x, start.z) == (steps[-1].x, steps[-1].z) and (goal.x, goal.z) == (up["x"], up["z"])) or \
                   ((goal.x, goal.z) == (steps[-1].x, steps[-1].z) and (start.x, start.z) == (up["x"], up["z"])):
                    return True
                # 检查台阶之间过渡
                for i in range(len(steps) - 1):
                    if ((start.x, start.z) == (steps[i].x, steps[i].z) and (goal.x, goal.z) == (steps[i+1].x, steps[i+1].z)) or \
                       ((goal.x, goal.z) == (steps[i].x, steps[i].z) and (start.x, start.z) == (steps[i+1].x, steps[i+1].z)):
                        return True
        except Exception:
            pass

        # 2. 普通单层平面边校验
        if start.y == goal.y:
            result = self.find_path(
                start, goal, player_sample=player_sample, occupied=occupied,
                movement_mode=movement_mode,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )
            return bool(result.get("reachable") and len(result.get("path") or []) == 2)

        return False"""

# Do replacement cleanly
idx = text.find("def has_candidate_edge(")
idx_end = text.find("def snap(", idx)
text = text[:idx] + new_has_candidate + "\n\n    " + text[idx_end:]

with open("backend/black2/world/static_navigation.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Updated has_candidate_edge to check staircase edges first!")
