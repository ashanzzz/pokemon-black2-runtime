import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_static_provider

prov = navigation_static_provider()
print("Inspecting route tiles from (14, 0, 46) to (15, 2, 44):")

# Segment 1: (14, 46) -> (10, 46)
for x in range(14, 9, -1):
    surf0 = prov.surface_at(457, x, 46, y=0)
    surf1 = prov.surface_at(457, x, 46, y=1)
    surf2 = prov.surface_at(457, x, 46, y=2)
    print(f"X={x}, Z=46: Y0(tc={hex(surf0.get('tile_class',0))}, mat={surf0.get('material',{}).get('kind')}) | Y1(tc={hex(surf1.get('tile_class',0))}, mat={surf1.get('material',{}).get('kind')}) | Y2(tc={hex(surf2.get('tile_class',0))}, mat={surf2.get('material',{}).get('kind')})")

print("\nSegment 2 & 3: on Y=2:")
for x in range(10, 16):
    s = prov.surface_at(457, x, 46, y=2)
    print(f"X={x}, Z=46, Y=2: tc={hex(s.get('tile_class',0))}, mat={s.get('material',{}).get('kind')}, blocks_bike={s.get('material',{}).get('blocks_cycling')}")

for z in range(46, 43, -1):
    s = prov.surface_at(457, 15, z, y=2)
    print(f"X=15, Z={z}, Y=2: tc={hex(s.get('tile_class',0))}, mat={s.get('material',{}).get('kind')}, blocks_bike={s.get('material',{}).get('blocks_cycling')}")
