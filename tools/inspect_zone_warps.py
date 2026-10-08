import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_static_provider

provider = navigation_static_provider()
zone = provider.rom.zone(457)
ent = provider.rom.entities(zone.entities_id)
print("Warp 0 in Zone 457:", ent['warps'][0])
