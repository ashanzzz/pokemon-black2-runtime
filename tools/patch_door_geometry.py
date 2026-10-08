with open('backend/black2/world/static_navigation.py', 'r', encoding='utf-8') as f:
    text = f.read()

old_block = '''    def _resolve_warp_doorstep(self, zone_id: int, wx: int, wz: int, ex: int = 1, ez: int = 1) -> tuple[int, int]:
        """Resolve a warp's navigable doorstep (门口可通行待命格).

        If the raw ROM coordinate is on an unwalkable wall, resolve to the
        adjacent walkable doorstep in front of the door (e.g. South).
        If already walkable (e.g. indoor door mat), keep it as is.
        """
        cell = self.surface_at(zone_id, wx, wz, 0, allow_unverified_terrain=True)
        if cell.get("walkable"):
            return wx, wz
        # In Gen-5 outdoor towns, buildings face South so the doorstep is South (z + 1)
        c_south = self.surface_at(zone_id, wx, wz + 1, 0, allow_unverified_terrain=True)
        if c_south.get("walkable"):
            return wx, wz + 1
        # Fallback to other adjacent tiles if any
        for d_x, d_z in ((0, -1), (1, 0), (-1, 0)):
            c_adj = self.surface_at(zone_id, wx + d_x, wz + d_z, 0, allow_unverified_terrain=True)
            if c_adj.get("walkable"):
                return wx + d_x, wz + d_z
        return wx, wz'''

new_block = '''    def resolve_door_geometry(self, zone_id: int, wx: int, wz: int, ex: int = 1, ez: int = 1) -> dict[str, Any]:
        """Resolve full geometry, doorstep candidates, and approach vector for a warp.

        Supports multi-tile doors (1-wide, 2-wide, 3-wide, 4-wide) and determines
        the approach/crossing vector (North, South, East, West).
        """
        ex = max(1, int(ex))
        ez = max(1, int(ez))

        walkable_footprint = []
        for x in range(wx, wx + ex):
            for z in range(wz, wz + ez):
                c = self.surface_at(zone_id, x, z, 0, allow_unverified_terrain=True)
                if c.get("walkable"):
                    walkable_footprint.append((x, z))

        if len(walkable_footprint) == ex * ez:
            return {
                "type": "walkable_mat",
                "width": ex,
                "height": ez,
                "width_category": f"{ex}_wide" if ex >= ez else f"{ez}_high",
                "facing": "any",
                "entry_direction": "directly",
                "entry_vector": {"dx": 0, "dz": 0},
                "doorsteps": walkable_footprint,
                "primary_doorstep": (wx, wz),
            }

        south_steps = [(x, wz + ez) for x in range(wx, wx + ex) if self.surface_at(zone_id, x, wz + ez, 0, allow_unverified_terrain=True).get("walkable")]
        north_steps = [(x, wz - 1) for x in range(wx, wx + ex) if self.surface_at(zone_id, x, wz - 1, 0, allow_unverified_terrain=True).get("walkable")]
        west_steps = [(wx - 1, z) for z in range(wz, wz + ez) if self.surface_at(zone_id, wx - 1, z, 0, allow_unverified_terrain=True).get("walkable")]
        east_steps = [(wx + ex, z) for z in range(wz, wz + ez) if self.surface_at(zone_id, wx + ex, z, 0, allow_unverified_terrain=True).get("walkable")]

        if len(south_steps) >= len(north_steps) and south_steps:
            facing, entry_dir, dx, dz, steps = "South", "North", 0, -1, south_steps
        elif north_steps:
            facing, entry_dir, dx, dz, steps = "North", "South", 0, 1, north_steps
        elif len(west_steps) >= len(east_steps) and west_steps:
            facing, entry_dir, dx, dz, steps = "West", "East", 1, 0, west_steps
        elif east_steps:
            facing, entry_dir, dx, dz, steps = "East", "West", -1, 0, east_steps
        else:
            facing, entry_dir, dx, dz, steps = "South", "North", 0, -1, [(wx, wz + ez)]

        return {
            "type": "building_portal",
            "width": ex,
            "height": ez,
            "width_category": f"{ex}_wide" if ex >= ez else f"{ez}_high",
            "facing": facing,
            "entry_direction": entry_dir,
            "entry_vector": {"dx": dx, "dz": dz},
            "doorsteps": steps,
            "primary_doorstep": steps[0] if steps else (wx, wz),
        }

    def _resolve_warp_doorstep(self, zone_id: int, wx: int, wz: int, ex: int = 1, ez: int = 1) -> tuple[int, int]:
        geom = self.resolve_door_geometry(zone_id, wx, wz, ex, ez)
        return geom["primary_doorstep"]'''

assert old_block in text, "old_block not found in static_navigation.py"
text = text.replace(old_block, new_block, 1)

old_overlay = '''                dx, dz = doorstep
                if dx <= tx < dx + ex and dz <= tz < dz + ez:
                    result.append({
                        "kind": "warp", "symbol": "D",
                        "warp_id": warp.get("id"),
                        "target_zone_id_candidate": warp.get("target_zone_or_map_raw"),
                        "position": {"x": dx, "z": dz},
                        "portal_position": {"x": wx, "z": wz},
                        "extent": {"x": ex, "z": ez},
                        "semantic_status": "ROM candidate; runtime transition not verified",
                    })'''

new_overlay = '''                dx, dz = doorstep
                geom = self.resolve_door_geometry(int(zone_id), wx, wz, ex, ez)
                in_footprint = (wx <= tx < wx + ex and wz <= tz < wz + ez)
                in_doorstep = (tx, tz) in geom["doorsteps"] or (dx <= tx < dx + ex and dz <= tz < dz + ez)
                if in_footprint or in_doorstep:
                    result.append({
                        "kind": "warp", "symbol": "D",
                        "warp_id": warp.get("id"),
                        "target_zone_id_candidate": warp.get("target_zone_or_map_raw"),
                        "position": {"x": dx, "z": dz},
                        "portal_position": {"x": wx, "z": wz},
                        "extent": {"x": ex, "z": ez},
                        "door_geometry": geom,
                        "approach_direction": geom["facing"],
                        "entry_direction": geom["entry_direction"],
                        "doorstep_candidates": geom["doorsteps"],
                        "semantic_status": "ROM candidate; runtime transition not verified",
                    })'''

assert old_overlay in text, "old_overlay not found in static_navigation.py"
text = text.replace(old_overlay, new_overlay, 1)

with open('backend/black2/world/static_navigation.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("static_navigation.py updated with door geometry successfully!")
