import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider

p = navigation_static_provider()
for zid in (429, 430, 431, 432, 433, 434):
    z = p.rom.zone(zid)
    ent = p.rom.entities(z.entities_id)
    print(f"Zone {zid}:")
    for n in ent.get('npcs', []):
        print(f"   npc id={n.get('id')}, sprite={n.get('sprite_id')}, script={n.get('script_id')}, pos=({n.get('x')}, {n.get('y')})")
