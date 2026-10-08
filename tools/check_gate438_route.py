import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.world.world_graph import WorldGraph

wg = WorldGraph()
wg.build()
blocked = {(448, 446), (446, 448), (456, 446), (446, 456), (437, 427), (427, 437)}
for z in list(wg._adj.keys()):
    wg._adj[z] = [(dst, k, m) for dst, k, m in wg._adj[z] if not (k == 'matrix_seam' and (z, dst) in blocked)]

r = wg.find_route(446, 427)
print("Real route from 446 to 427:", r.get("zone_path"))
for s in r.get("steps", []):
    print("  ", s.get("from_zone"), "->", s.get("to_zone"), f"({s.get('kind')})")
