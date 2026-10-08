import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfaces = g._decode_zone_surfaces(448)

by_coord = {(s["x"], s["z"]): s for s in surfaces}

water_cells = {}
land_cells = {}

for s in surfaces:
    surf = s["surface"]
    h = surf.get("height", {})
    col = surf.get("collision", {})
    mat = surf.get("material", {})
    kind = mat.get("kind")
    req = col.get("requires")
    rel_y = h.get("chunk_relative_world_y", 0.0)
    blocked = col.get("static_blocked")
    x, z = s["x"], s["z"]
    
    if kind == "water" or (req and "surf" in req):
        water_cells[(x, z)] = (rel_y, s)
    elif not blocked or kind != "obstacle":
        land_cells[(x, z)] = (rel_y, s)

print(f"Total water cells: {len(water_cells)}, candidate land cells: {len(land_cells)}")

surf_points = []
for (lx, lz), (l_y, ls) in land_cells.items():
    for dx, dz, direction in [(-1, 0, "West"), (1, 0, "East"), (0, -1, "North"), (0, 1, "South")]:
        wx, wz = lx + dx, lz + dz
        if (wx, wz) in water_cells:
            w_y, ws = water_cells[(wx, wz)]
            dy = abs((l_y or 0.0) - (w_y or 0.0))
            surf_points.append(((lx, lz), l_y, (wx, wz), w_y, dy, direction))

# Sort by dy (smallest height difference first)
surf_points.sort(key=lambda item: item[4])

print(f"\nTop 20 Surf Jump Points by minimal height delta:")
for land, ly, water, wy, dy, direction in surf_points[:25]:
    print(f"  Land {land} (y={ly:.1f}) facing {direction} -> Water {water} (y={wy:.1f}) dy={dy:.1f}")
