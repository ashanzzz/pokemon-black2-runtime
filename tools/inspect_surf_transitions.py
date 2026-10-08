import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfaces = g._decode_zone_surfaces(448)
by_coord = {(s["x"], s["z"]): s for s in surfaces}

print("Searching for Land -> Water Surf edges in Zone 448...")
valid_surf_edges = []

for s in surfaces:
    lx, lz = s["x"], s["z"]
    l_surf = s["surface"]
    l_mat = l_surf.get("material", {})
    l_col = l_surf.get("collision", {})
    l_h = l_surf.get("height", {})
    
    lk = l_mat.get("kind")
    if lk in ("water", "obstacle"):
        continue
        
    for dx, dz, direction in [(-1, 0, "West"), (1, 0, "East"), (0, -1, "North"), (0, 1, "South")]:
        wx, wz = lx + dx, lz + dz
        ws = by_coord.get((wx, wz))
        if not ws:
            continue
        w_surf = ws["surface"]
        w_mat = w_surf.get("material", {})
        w_col = w_surf.get("collision", {})
        w_h = w_surf.get("height", {})
        
        # Must be water
        if w_mat.get("kind") != "water" and "surf" not in (w_col.get("requires") or []):
            continue
            
        blocked_dirs = l_col.get("blocked_directions") or []
        if direction in blocked_dirs:
            continue
            
        dy = abs((l_h.get("chunk_relative_world_y") or 0.0) - (w_h.get("chunk_relative_world_y") or 0.0))
        valid_surf_edges.append(((lx, lz), lk, (wx, wz), direction, dy))

print(f"Found {len(valid_surf_edges)} valid Land -> Water surf edges:")
valid_surf_edges.sort(key=lambda x: x[4])
for land, lk, water, d, dy in valid_surf_edges[:25]:
    print(f"  Land {land} ({lk}) facing {d} -> Water {water} dy={dy:.1f}")
