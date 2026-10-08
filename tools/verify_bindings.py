import re

with open("frontend/v2.html", "r", encoding="utf-8") as f:
    html = f.read()

with open("frontend/v2.js", "r", encoding="utf-8") as f:
    js = f.read()

handlers = re.findall(r'(?:onclick|oninput|onmouseenter|onchange)\s*=\s*"([^"]+)"', html)
functions = set()
for h in handlers:
    calls = re.findall(r'([a-zA-Z0-9_$]+)\s*\(', h)
    for c in calls:
        if c not in ('document', 'parseInt', 'alert'):
            functions.add(c)

print("Found handler functions in v2.html:", sorted(functions))

missing = []
for f in sorted(functions):
    if f"function {f}" not in js and f"{f} = " not in js and f"async function {f}" not in js:
        missing.append(f)

if missing:
    print("MISSING FUNCTIONS in v2.js:", missing)
else:
    print("ALL inline handler functions are DEFINED in v2.js!")
