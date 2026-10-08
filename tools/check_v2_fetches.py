import re
with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

fetches = re.findall(r'fetch\([\'\"\`]([^\'\"\`]+)[\'\"\`]', text)
print("Unique fetch URLs in v2.js:")
for u in sorted(set(fetches)):
    print(" ", u)
