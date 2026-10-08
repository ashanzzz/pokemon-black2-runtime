import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph
from backend.black2.world.map_truth import MapTruthService

g = RomStaticNavigationGraph()
print("ROM Static Navigation Graph loaded.")

# Inspect Zone 447
z447_nodes = [n for n in g.nodes.values() if n.zone_id == 447]
print(f"Zone 447 nodes count: {len(z447_nodes)}")
for n in z447_nodes[:10]:
    print(" ", n.x, n.y, n.z, "elevation:", n.elevation, "flags:", hex(n.movement_flags or 0), "water:", n.is_water)

# Inspect Zone 448
z448_nodes = [n for n in g.nodes.values() if n.zone_id == 448]
print(f"Zone 448 nodes count: {len(z448_nodes)}")
water_nodes = [n for n in z448_nodes if n.is_water]
land_nodes = [n for n in z448_nodes if not n.is_water]
print(f"Zone 448 water nodes: {len(water_nodes)}, land nodes: {len(land_nodes)}")

# Find connections/warps between 447 and 448
edges_between = []
for (u, v), edge in g.edges.items():
    nu = g.nodes.get(u)
    nv = g.nodes.get(v)
    if nu and nv:
        if (nu.zone_id == 447 and nv.zone_id == 448) or (nu.zone_id == 448 and nv.zone_id == 447):
            edges_between.append((nu, nv, edge))

print(f"Edges between 447 and 448: {len(edges_between)}")
for nu, nv, e in edges_between[:10]:
    print(f"  Zone {nu.zone_id} ({nu.x},{nu.y},{nu.z}) -> Zone {nv.zone_id} ({nv.x},{nv.y},{nv.z}) type: {e.edge_type}")
