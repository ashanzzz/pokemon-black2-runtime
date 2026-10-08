import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfaces = g._decode_zone_surfaces(448)

water_coords = set()
edge_coords = set()

for s in surfaces:
    mat = s["surface"].get("material", {})
    kind = mat.get("kind")
    col = s["surface"].get("collision", {})
    req = col.get("requires") or []
    if kind == "water" or "surf" in req:
        water_coords.add((s["x"], s["z"]))
    elif kind == "water_edge":
        edge_coords.add((s["x"], s["z"]))

print(f"Water tiles ({len(water_coords)}):")
print(f"X range: {min(x for x, z in water_coords)}..{max(x for x, z in water_coords)}")
print(f"Z range: {min(z for x, z in water_coords)}..{max(z for x, z in water_coords)}")

print(f"\nWater edge tiles ({len(edge_coords)}):")
print(f"X range: {min(x for x, z in edge_coords)}..{max(x for x, z in edge_coords)}")
print(f"Z range: {min(z for x, z in edge_coords)}..{max(z for x, z in edge_coords)}")

# Print all edge coords
for x, z in sorted(edge_coords):
    print(f"  Edge: ({x}, {z})")
