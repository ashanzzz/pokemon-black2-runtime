# Fix 1 in static_navigation.py:
with open('backend/black2/world/static_navigation.py', 'r', encoding='utf-8') as f:
    text = f.read()

old_cond = 'if in_footprint or in_doorstep:'
new_cond = 'if (tx, tz) in geom["doorsteps"]:'

assert old_cond in text, "old_cond not found"
text = text.replace(old_cond, new_cond, 1)

with open('backend/black2/world/static_navigation.py', 'w', encoding='utf-8') as f:
    f.write(text)

# Fix 2 in navigation_routes.py:
with open('backend/black2/api/navigation_routes.py', 'r', encoding='utf-8') as f:
    text2 = f.read()

old_probe = """            if provider is not None and nx is not None and nz is not None:
                nsurf = provider.surface_at(int(n_zone), int(nx), int(nz), int(ny), allow_unverified_terrain=True)"""

new_probe = """            if provider is not None and nx is not None and nz is not None:
                try:
                    nsurf = provider.surface_at(int(n_zone), int(nx), int(nz), int(ny), allow_unverified_terrain=True)
                except Exception:
                    nsurf = None"""

assert old_probe in text2, "old_probe not found"
text2 = text2.replace(old_probe, new_probe, 1)

with open('backend/black2/api/navigation_routes.py', 'w', encoding='utf-8') as f:
    f.write(text2)

print("Both fixes applied!")
