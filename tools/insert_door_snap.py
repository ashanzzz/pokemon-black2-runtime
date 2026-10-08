with open("backend/black2/world/navigation_planning.py", "r", encoding="utf-8") as f:
    text = f.read()

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

target_str = "    def _resolve_start_node(self, start_position: dict[str, Any], player: dict[str, Any] | None = None) -> tuple[NavNode, dict[str, Any]]:"
if target_str in text and "def _maybe_snap_door_portal" not in text:
    text = text.replace(target_str, helper + target_str)
    with open("backend/black2/world/navigation_planning.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Inserted _maybe_snap_door_portal before _resolve_start_node")
else:
    print("Already inserted or target not found")
