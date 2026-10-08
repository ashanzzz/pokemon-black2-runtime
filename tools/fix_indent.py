with open("backend/black2/world/static_navigation.py", "r", encoding="utf-8") as f:
    text = f.read()

# Replace 8 spaces on def has_candidate_edge
text = text.replace("        def has_candidate_edge(\n", "    def has_candidate_edge(\n")

with open("backend/black2/world/static_navigation.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Fixed def has_candidate_edge indentation")
