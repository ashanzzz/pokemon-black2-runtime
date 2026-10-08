with open("backend/black2/api/pc_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace('f"??{m.get', 'f"槽位{m.get')
with open("backend/black2/api/pc_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Fixed current_moves label!")