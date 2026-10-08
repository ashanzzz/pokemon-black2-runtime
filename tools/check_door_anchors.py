with open("backend/black2/world/navigation_tasks.py", "r", encoding="utf-8") as f:
    text = f.read()

print("Anchor arrived:", 'arrived, arrival_player = self._current_node()\n        goal = path[-1]' in text)
print("Anchor landing_divergent:", 'if node is None or not self._in_spatial_set(node, allowed):' in text)
