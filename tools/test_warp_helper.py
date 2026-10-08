import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_static_provider

provider = navigation_static_provider()

def inspect_tile_warp(zone_id, x, z):
    overlays = list(provider.event_overlay_at(zone_id, x, z))
    warps = [it for it in overlays if it.get("kind") == "warp"]
    print(f"Tile ({x}, {z}) in Zone {zone_id}:")
    for w in warps:
        geom = w.get("door_geometry", {})
        print(f"  Warp -> Zone {w.get('target_zone_id_candidate')}: Type={geom.get('type')}, EntryDir={geom.get('entry_direction')}, Facing={geom.get('facing')}")
    if not warps:
        print("  No warp directly on this tile.")

# 1. 工业园大门 (457, 18, 17)
inspect_tile_warp(457, 18, 17)
# 2. 算木镇宝可梦中心待命格 (439, 105, 694)
inspect_tile_warp(439, 105, 694)
# 3. 算木镇宝可梦中心门洞 (439, 105, 693)
inspect_tile_warp(439, 105, 693)
