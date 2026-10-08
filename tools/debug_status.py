import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.fast_travel import fast_travel_service

dests = fast_travel_service.get_destinations()
print("Total dests:", len(dests))
for d in dests[:5]:
    print("  Zone:", d.get("zone_id"), "Status:", d.get("status"), "Flyable:", d.get("flyable"))

avail = fast_travel_service.get_destinations(status="flyable")
print("Avail count with status='flyable':", len(avail))

avail2 = fast_travel_service.get_destinations(status="unlocked")
print("Avail count with status='unlocked':", len(avail2))