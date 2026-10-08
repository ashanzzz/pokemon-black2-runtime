import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider

p = navigation_static_provider()
# Chunk 5 is x in [160, 191]. Chunk 6 is x in [192, 223].
# The border is between x=191 and x=192 across z in [640, 671].
walkable_seams = []
for z in range(640, 672):
    s191 = p.surface_at(446, 191, z, 0)
    s192 = p.surface_at(448, 192, z, 0)
    w191 = s191.get('walkable', False)
    w192 = s192.get('walkable', False)
    if w191 and w192:
        walkable_seams.append(z)

print(f"Walkable seam tiles across x=191/192 (Zone 446 <-> Zone 448): {len(walkable_seams)}")
print(f"Seam z-coordinates: {walkable_seams}")
