with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

old_snippet = """      const pCoordBadge = document.getElementById('playerCoordBadge');
      if (pCoordBadge) {
        pCoordBadge.innerText = `主角: (X=${livePlayerGrid.x}, Z=${livePlayerGrid.z}, Y=${livePlayerGrid.y ?? 0})`;
      }"""

new_snippet = """      const pCoordBadge = document.getElementById('playerCoordBadge');
      if (pCoordBadge) {
        pCoordBadge.innerText = `主角: (X=${livePlayerGrid.x}, Z=${livePlayerGrid.z}, Y=${livePlayerGrid.y ?? 0})`;
      }
      const navStart = document.getElementById('navStartCoord');
      if (navStart && (!navStart.innerText || navStart.innerText.includes('等待读取'))) {
        navStart.innerText = `X=${livePlayerGrid.x}, Y=${livePlayerGrid.y ?? 0}, Z=${livePlayerGrid.z}`;
      }"""

if "navStart && (!navStart.innerText || navStart.innerText.includes('等待读取'))" not in text:
    text = text.replace(old_snippet, new_snippet)
    with open("frontend/v2.js", "w", encoding="utf-8") as f:
        f.write(text)
    print("Patched navStartCoord auto-sync in pollPlayerRuntime")
else:
    print("Already present")
