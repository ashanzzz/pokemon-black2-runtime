import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_static_provider

prov = navigation_static_provider()
print("Overlays at (457, 18, 17):", list(prov.event_overlay_at(457, 18, 17)))
surf = prov.surface_at(457, 18, 17, y=0)
print("Surface at (457, 18, 17):", surf.get("walkable"), surf.get("kind"), surf.get("symbol"))
