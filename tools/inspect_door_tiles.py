import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_static_provider

prov = navigation_static_provider()
for z in range(690, 696):
    surf = prov.surface_at(456, 211, z, y=0)
    print(f"Z={z}: walkable={surf.get('walkable')} blocked={surf.get('blocked')} tclass={hex(surf.get('tile_class', 0))} flags={surf.get('flags')} kind={surf.get('kind')} symbol={surf.get('symbol')}")
