with open("backend/black2/api/pc_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace("???", " (首发)")
with open("backend/black2/api/pc_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Fixed lineup label!")