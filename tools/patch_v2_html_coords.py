with open("frontend/v2.html", "r", encoding="utf-8") as f:
    text = f.read()

# In header of [02.1]
old_header_badges = """              <span class="ds-badge ds-badge-cyan" id="layerSummaryBadge">正在读取立体标高层...</span>"""

new_header_badges = """              <span class="ds-badge ds-badge-cyan" id="playerCoordBadge" style="font-family:var(--font-mono); font-weight:700;">主角: X=--, Z=--, Y=--</span>
              <span class="ds-badge ds-badge-amber" id="targetCoordBadge" style="font-family:var(--font-mono); font-weight:700;">目标: (未选定)</span>
              <span class="ds-badge" id="layerSummaryBadge">正在读取立体标高层...</span>"""

if "playerCoordBadge" not in text:
    text = text.replace(old_header_badges, new_header_badges)
    with open("frontend/v2.html", "w", encoding="utf-8") as f:
        f.write(text)
    print("v2.html updated with playerCoordBadge and targetCoordBadge")
else:
    print("playerCoordBadge already present in v2.html")
