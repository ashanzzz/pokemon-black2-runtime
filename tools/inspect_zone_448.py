import sys, os
sys.path.insert(0, os.getcwd())

from collections import Counter
from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfaces = g._decode_zone_surfaces(448)

kinds = Counter()
requires_counter = Counter()
block_counter = Counter()

water_cells = []
walkable_cells = []

for s in surfaces:
    surf = s.get("surface") or {}
    mat = surf.get("material") or {}
    col = surf.get("collision") or {}
    
    k = mat.get("kind")
    req = tuple(col.get("requires") or [])
    blocked = col.get("static_blocked")
    
    kinds[k] += 1
    requires_counter[req] += 1
    block_counter[blocked] += 1
    
    x, z = s["x"], s["z"]
    if k in ("water", "water_edge") or "surf" in req:
        water_cells.append((x, z, k, req, blocked))
    elif not blocked:
        walkable_cells.append((x, z, k))

print("Kinds:", kinds)
print("Requires:", requires_counter)
print("Blocked:", block_counter)
print(f"Walkable land cells: {len(walkable_cells)}")
print(f"Water cells: {len(water_cells)}")

water_set = {(x, z) for x, z, _, _, _ in water_cells}
walk_set = {(x, z) for x, z, _ in walkable_cells}

shores = []
for lx, lz in walk_set:
    for dx, dz, direction in [(-1, 0, "West"), (1, 0, "East"), (0, -1, "North"), (0, 1, "South")]:
        adj = (lx + dx, lz + dz)
        if adj in water_set:
            shores.append(((lx, lz), adj, direction))

print(f"Shore transitions (Land -> Water): {len(shores)}")
for land, water, direction in shores[:15]:
    print(f"  Land {land} facing {direction} -> Water {water}")
