import re

with open("frontend/v2.html", "r", encoding="utf-8") as f:
    html = f.read()

with open("frontend/v2.js", "r", encoding="utf-8") as f:
    js = f.read()

clicks = re.findall(r'onclick=[\"\']([a-zA-Z0-9_]+)\s*\(', html)
for fn in sorted(set(clicks)):
    has_fn = re.search(r'\bfunction\s+' + fn + r'\b', js) or re.search(r'\b' + fn + r'\s*=', js) or re.search(r'\bwindow\.' + fn + r'\s*=', js)
    if not has_fn:
        print(f"MISSING FUNCTION: {fn}")
    else:
        print(f"Found function: {fn}")
