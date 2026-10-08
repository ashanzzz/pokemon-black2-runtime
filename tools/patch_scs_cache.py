with open("backend/black2/world/staircase_corridors.py", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace(
    "surfaces = provider._decode_zone_surfaces(zone_id)",
    "surfaces = provider._zone_surfaces(zone_id) if hasattr(provider, '_zone_surfaces') else provider._decode_zone_surfaces(zone_id)"
)

with open("backend/black2/world/staircase_corridors.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Updated staircase_corridors.py to use cached _zone_surfaces")
