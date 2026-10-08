import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_static_provider

prov = navigation_static_provider()
print("Inspecting (11, 45) across layers:")
s0 = prov.surface_at(457, 11, 45, y=0)
s1 = prov.surface_at(457, 11, 45, y=1)
s2 = prov.surface_at(457, 11, 45, y=2)
print("Y=0:", s0.get("walkable"), s0.get("status"), s0.get("tile_class"))
print("Y=1:", s1.get("walkable"), s1.get("status"), s1.get("tile_class"))
print("Y=2:", s2.get("walkable"), s2.get("status"), s2.get("tile_class"))

# Check directional barriers on (11, 46) and (11, 45)
c_46 = prov._planning_cells(457, 2).get((11, 46))
c_45 = prov._planning_cells(457, 2).get((11, 45))
print("\nCell (11, 46) on Y=2:")
print("  blocked_directions:", c_46.blocked_directions if c_46 else None)
print("  static_blocked:", c_46.static_blocked if c_46 else None)
print("  tile_class:", hex(c_46.tile_class) if c_46 else None)

print("\nCell (11, 45) on Y=2:")
print("  in cells:", c_45 is not None)
print("  blocked_directions:", c_45.blocked_directions if c_45 else None)
print("  static_blocked:", c_45.static_blocked if c_45 else None)
print("  tile_class:", hex(c_45.tile_class) if c_45 else None)
