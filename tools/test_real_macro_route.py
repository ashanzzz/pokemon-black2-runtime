import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.world.world_graph import WorldGraph

wg = WorldGraph()
wg.build()
blocked = {(448, 446), (446, 448), (456, 446), (446, 456)}
for z in list(wg._adj.keys()):
    wg._adj[z] = [(dst, k, m) for dst, k, m in wg._adj[z] if not (k == 'matrix_seam' and (z, dst) in blocked)]

r = wg.find_route(457, 427)
print("Path without fake seams:", r.get("zone_path"))
for step in r.get("steps", []):
    print("  ", step.get("from_zone"), "->", step.get("to_zone"), f"({step.get('kind')})")
