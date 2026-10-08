import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_static_provider

prov = navigation_static_provider()
print("Overlays at (456, 211, 693):", list(prov.event_overlay_at(456, 211, 693)))
print("Overlays at (456, 211, 692):", list(prov.event_overlay_at(456, 211, 692)))
