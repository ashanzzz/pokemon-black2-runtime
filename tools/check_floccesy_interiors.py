import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider

p = navigation_static_provider()
for zid in (441, 442, 443):
    z = p.rom.zone(zid)
    ent = p.rom.entities(z.entities_id)
    print(f"Zone {zid}: npcs={len(ent.get('npcs', []))}, warps={len(ent.get('warps', []))}")
    for w in ent.get('warps', []):
        print(f"   warp -> Zone {w.get('target_zone_or_map_raw')}")
