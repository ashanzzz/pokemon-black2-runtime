with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Add handleRadiusChange
if "function handleRadiusChange" not in text:
    radius_func = """
function handleRadiusChange(radius) {
  currentRadarRadius = parseInt(radius, 10);
  const diameters = { 4: '9x9 (半径4·标准)', 7: '15x15 (半径7·战术)', 10: '21x21 (半径10·宏观)', 15: '31x31 (半径15·全域)' };
  appendLog(`[雷达视口] 切换视口范围为 ${diameters[currentRadarRadius] || currentRadarRadius}...`);
  pollRadar();
}
"""
    text = radius_func + "\n" + text

# 2. Fix Door D in renderMultiLayerGrid:
# Replace:
#   } else if (isWarpTile) {
#     sym = 'D';
#     cellClass = 'cell-warp';
#   }
# With:
#   } else if (rawSym === 'D' || cell.is_portal_doorway === true) {
#     sym = 'D';
#     cellClass = 'cell-warp';
#   }
old_warp = """        } else if (isWarpTile) {
          sym = 'D';
          cellClass = 'cell-warp';"""
new_warp = """        } else if (rawSym === 'D' || cell.is_portal_doorway === true) {
          sym = 'D';
          cellClass = 'cell-warp';"""
text = text.replace(old_warp, new_warp)

# 3. Add radius class to ds-map-slice in renderMultiLayerGrid:
# Replace:
#   let sliceHtml = `
#     <div class="ds-map-slice" id="slice-floor-${fy}">
# With:
#   let sliceHtml = `
#     <div class="ds-map-slice radius-${currentRadarRadius}" id="slice-floor-${fy}">
text = text.replace('<div class="ds-map-slice" id="slice-floor-${fy}">', '<div class="ds-map-slice radius-${currentRadarRadius}" id="slice-floor-${fy}">')

# 4. Path node lookup fallback in renderMultiLayerGrid:
old_path_lookup = "const pathNode = activePlannedPathMap.get(cellKey);"
new_path_lookup = "const pathNode = activePlannedPathMap.get(cellKey) || activePlannedPathMap.get(`${cell.x},${cell.z}`);"
text = text.replace(old_path_lookup, new_path_lookup)

# 5. Double-key activePlannedPathMap in handlePlanNav:
old_node_set = """      nodes.forEach((n, idx) => {
        const isGoal = (idx === nodes.length - 1);
        activePlannedPathMap.set(`${n.x},${n.z},${n.y}`, { step: idx + 1, total: nodes.length, isGoal: isGoal });
      });"""

new_node_set = """      nodes.forEach((n, idx) => {
        const isGoal = (idx === nodes.length - 1);
        const nodeY = (n.y !== undefined && n.y !== null) ? n.y : ty;
        activePlannedPathMap.set(`${n.x},${n.z},${nodeY}`, { step: idx + 1, total: nodes.length, isGoal: isGoal });
        activePlannedPathMap.set(`${n.x},${n.z}`, { step: idx + 1, total: nodes.length, isGoal: isGoal });
      });"""
text = text.replace(old_node_set, new_node_set)

# 6. Add keyboard shortcut for log drawer (Backquote)
if "e.key === '`'" not in text:
    shortcut_code = """
window.addEventListener('keydown', (e) => {
  if (e.key === '`' || e.key === '~') {
    if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
      e.preventDefault();
      toggleGlobalLogDrawer();
    }
  }
});
"""
    text += shortcut_code

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js patched successfully.")
