import urllib.request, json, sys, os
sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
from backend.black2.decoders.trainer_rom import decode_gen5_message_file

p = navigation_static_provider()
zone = p.rom.zone(433)
print("Zone 433 Header:")
print("  name_id:", zone.location_name_id)
print("  parent_zone_id:", zone.parent_zone_id)
print("  text_file_id:", zone.text_file_id)
print("  scripts_id:", zone.scripts_id)
print("  entities_id:", zone.entities_id)

# Decode text archive
try:
    arc = p.rom.rom.archive("a/0/0/2")
    msg_entries = decode_gen5_message_file(arc.files[zone.text_file_id])[0]
    print(f"Text strings in Zone 433 (total {len(msg_entries)}):")
    for idx, m in enumerate(msg_entries[:10]):
        print(f"  [{idx}]: {m.get('text')}")
except Exception as e:
    print("Error decoding text:", e)

# Decode entities
ent = p.rom.entities(zone.entities_id)
print(f"Entities in Zone 433:")
print("  NPCs:", ent.get("npcs"))
print("  Furniture:", ent.get("furniture"))
print("  Warps:", ent.get("warps"))
