with open("backend/black2/world/static_navigation.py", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Update _anchor_from_sample to include tile_under
old_anchor_body = """        return {
            "grid": {"x": gx, "y": gy, "z": gz},
            "world": {"x": wx, "y": wy, "z": wz},
            "chunk": {"x": gx // CHUNK_TILES, "z": gz // CHUNK_TILES},
        }"""

new_anchor_body = """        live_tile = position.get("tile_under") or sample.get("environment", {}).get("tile_under") or sample.get("tile_under") or {}
        return {
            "grid": {"x": gx, "y": gy, "z": gz},
            "world": {"x": wx, "y": wy, "z": wz},
            "chunk": {"x": gx // CHUNK_TILES, "z": gz // CHUNK_TILES},
            "tile_under": live_tile,
        }"""
text = text.replace(old_anchor_body, new_anchor_body)

# 2. Update _anchor_relative_height: if candidates all agree on height, return it
old_anchor_rel = """            candidates.append(float(height))
        return candidates[0] if len(candidates) == 1 else None"""

new_anchor_rel = """            candidates.append(float(height))
        if candidates and all(abs(c - candidates[0]) < 0.1 for c in candidates):
            return candidates[0]
        return candidates[0] if len(candidates) == 1 else None"""
text = text.replace(old_anchor_rel, new_anchor_rel)

# 3. In _cells_for_layer: fallback to relative / TILE_WORLD when anchor_relative is None
old_layer_fallback = """            elif len({(x["x"], x["z"]) for x in surfaces}) and anchor is None:
                # With no height anchor, a multi-layer map cannot safely map
                # terrain layer indices to GPos.y.  A single flat surface per
                # tile remains usable for a read-only/static preview.
                pass"""

new_layer_fallback = """            elif relative is not None:
                candidate_y = int(round(relative / TILE_WORLD))
                if candidate_y != int(y):
                    continue"""
text = text.replace(old_layer_fallback, new_layer_fallback)

# 4. At the end of _cells_for_layer, inject staircase handrails
old_return_cells = """        return cells"""
new_return_cells = """        # 自动注入阶梯护栏物理阻断：东西向阶梯阻断南北(up/down)，南北向阶梯阻断东西(left/right)
        try:
            from .staircase_corridors import StaircaseCorridorService
            scs = StaircaseCorridorService(self)
            for c in scs.analyze_zone(int(zone_id)):
                for step in c.steps:
                    ckey = (int(step.x), int(step.z))
                    if ckey in cells:
                        exist = cells[ckey]
                        handrail_blocked = ("up", "down") if c.axis == "east_west" else ("left", "right")
                        new_blocked = tuple(sorted(set(exist.blocked_directions + handrail_blocked)))
                        cells[ckey] = StaticNavigationCell(
                            node=exist.node, layer_index=exist.layer_index, tile_class=exist.tile_class,
                            flags=exist.flags, static_blocked=exist.static_blocked,
                            blocked_directions=new_blocked, ledge_direction=exist.ledge_direction,
                            material=exist.material, source=exist.source, relative_height=exist.relative_height,
                        )
        except Exception:
            pass
        return cells"""
# Replace only the return cells inside _cells_for_layer
idx = text.find("def _cells_for_layer(")
idx_end = text.find("def _planning_cells(", idx)
slice_cells = text[idx:idx_end]
slice_cells = slice_cells.replace(old_return_cells, new_return_cells, 1)
text = text[:idx] + slice_cells + text[idx_end:]

with open("backend/black2/world/static_navigation.py", "w", encoding="utf-8") as f:
    f.write(text)

print("static_navigation.py patched with elevation candidate_y filtering and staircase handrail guards!")
