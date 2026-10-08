import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_static_provider

prov = navigation_static_provider()
records_46 = prov._surface_records(457, 11, 46)
records_45 = prov._surface_records(457, 11, 45)

print("Surface records at (11, 46):")
for r in records_46:
    s = r["surface"]
    print("  layer:", r.get("layer_index"), "height:", s.get("height"), "raw:", s.get("raw"))

print("\nSurface records at (11, 45):")
for r in records_45:
    s = r["surface"]
    print("  layer:", r.get("layer_index"), "height:", s.get("height"), "raw:", s.get("raw"))
