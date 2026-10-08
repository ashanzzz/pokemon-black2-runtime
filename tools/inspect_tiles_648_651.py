import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
p = navigation_static_provider()
for z in [647, 648, 649, 650, 651]:
    surf = p.surface_at(448, 210, z, 0)
    overlays = p.event_overlay_at(448, 210, z)
    print(f"Z={z}: walkable={surf.get('walkable')} kind={surf.get('kind')} class={hex(surf.get('tile_class', 0))} overlays={overlays}")
