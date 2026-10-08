import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_static_provider

prov = navigation_static_provider()
print("Inspecting surfaces in Zone 457 around X=10..20, Z=40..50:")

for z in range(42, 50):
    for x in range(11, 16):
        s0 = prov.surface_at(457, x, z, y=0)
        s2 = prov.surface_at(457, x, z, y=2)
        s1 = prov.surface_at(457, x, z, y=1)
        w0 = s0.get("walkable")
        w2 = s2.get("walkable")
        w1 = s1.get("walkable")
        tc0 = hex(s0.get("tile_class", 0))
        tc2 = hex(s2.get("tile_class", 0))
        tc1 = hex(s1.get("tile_class", 0))
        sym0 = s0.get("symbol")
        sym2 = s2.get("symbol")
        print(f"({x}, {z}): Y0(w={w0}, tc={tc0}, sym={sym0}) | Y1(w={w1}, tc={tc1}) | Y2(w={w2}, tc={tc2}, sym={sym2})")
