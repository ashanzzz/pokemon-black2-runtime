import re
with open("frontend/v2.html", "r", encoding="utf-8") as f:
    text = f.read()

clicks = re.findall(r'onclick=[\"\']([^\"\']+)[\"\']', text)
funcs = set(c.split('(')[0].strip() for c in clicks)
print("Total onclicks in HTML:", len(clicks))
print("Distinct onclick functions in HTML:", sorted(funcs))

with open("frontend/v2.js", "r", encoding="utf-8") as f:
    js_text = f.read()

missing = []
for fn in funcs:
    if f"function {fn}" not in js_text and f"{fn} =" not in js_text and f"window.{fn}" not in js_text:
        missing.append(fn)

print("Functions missing in v2.js:", missing)
