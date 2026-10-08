with open("backend/black2/world/navigation_planning.py", "r", encoding="utf-8") as f:
    text = f.read()

old_func_end = """        return NavNode(int(zone_id), x, y, z), {"input_type": "global_grid", "matrix_id": matrix_id, "zone_resolved": True}"""

new_logic = """        resolved_node = NavNode(int(zone_id), x, y, z)
        resolved_meta = {"input_type": "global_grid", "matrix_id": matrix_id, "zone_resolved": True}
        return self._maybe_snap_door_portal(resolved_node, resolved_meta)"""

text = text.replace(old_func_end, new_logic)

old_grid_return = """            return node, {"input_type": "grid", "matrix_id": self._matrix_id_for_zone(node.zone_id), "zone_resolved": False}"""
new_grid_return = """            meta = {"input_type": "grid", "matrix_id": self._matrix_id_for_zone(node.zone_id), "zone_resolved": False}
            return self._maybe_snap_door_portal(node, meta)"""
text = text.replace(old_grid_return, new_grid_return)

helper = """    def _maybe_snap_door_portal(self, node: NavNode, meta: dict[str, Any]) -> tuple[NavNode, dict[str, Any]]:
        \"\"\"Snap unwalkable building door portals directly to their reachable doorstep.\"\"\"
        provider = self._global_provider()
        if provider and hasattr(provider, "event_overlay_at"):
            try:
                overlays = list(provider.event_overlay_at(int(node.zone_id), int(node.x), int(node.z)))
                warp = next((it for it in overlays if it.get("kind") == "warp"), None)
                if warp:
                    geom = warp.get("door_geometry") or {}
                    doorsteps = geom.get("doorsteps") or []
                    surf = provider.surface_at(int(node.zone_id), int(node.x), int(node.z), y=int(node.y)) if hasattr(provider, "surface_at") else {}
                    if (geom.get("type") == "building_portal" or not surf.get("walkable")) and doorsteps:
                        best_ds = min(doorsteps, key=lambda ds: abs(ds[0] - node.x) + abs(ds[1] - node.z) + (0 if ds[0] == node.x else 10))
                        original_target = node.public()
                        node = NavNode(int(node.zone_id), best_ds[0], int(node.y), best_ds[1])
                        meta["original_door_target"] = original_target
                        meta["door_geometry"] = geom
                        meta["is_door_approach"] = True
            except Exception:
                pass
        return node, meta
"""

if "_maybe_snap_door_portal" not in text:
    text = text.replace("    def _resolve_start_node(", helper + "\n    def _resolve_start_node(")

with open("backend/black2/world/navigation_planning.py", "w", encoding="utf-8") as f:
    f.write(text)

print("navigation_planning.py patched with _maybe_snap_door_portal")
