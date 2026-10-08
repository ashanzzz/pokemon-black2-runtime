import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_static_provider

provider = navigation_static_provider()
print("event_overlay_at (457, 18, 18):")
for item in provider.event_overlay_at(457, 18, 18):
    print(" ", item)

print("\nSearching all warps in zone 457 from provider:")
for x in range(14, 23):
    for z in range(16, 20):
        items = list(provider.event_overlay_at(457, x, z))
        warps = [it for it in items if it.get("kind") == "warp"]
        if warps:
            print(f"  Found warp at ({x}, {z}):", warps)
