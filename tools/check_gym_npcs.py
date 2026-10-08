import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider

p = navigation_static_provider()
z489 = p.rom.zone(489)
ent = p.rom.entities(z489.entities_id)
for n in ent.get('npcs', []):
    print(f"NPC {n.get('id')}: sprite={n.get('sprite_id')}, script={n.get('script_id')}, sight={n.get('sight_raw')}, pos=({n.get('x')}, {n.get('y')}), dir={n.get('direction_raw')}")
