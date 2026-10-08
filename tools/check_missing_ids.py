import re

with open("frontend/v2.js", "r", encoding="utf-8") as f:
    js = f.read()

with open("frontend/v2.html", "r", encoding="utf-8") as f:
    html = f.read()

ids_in_js = re.findall(r"document\.getElementById\(['\"]([^'\"]+)['\"]\)", js)
unique_ids = sorted(set(ids_in_js))

print(f"Total getElementById in JS: {len(ids_in_js)} ({len(unique_ids)} unique)")

missing_ids = []
for el_id in unique_ids:
    if f'id="{el_id}"' not in html and f"id='{el_id}'" not in html:
        missing_ids.append(el_id)

print(f"Missing IDs in HTML ({len(missing_ids)}):", missing_ids)
