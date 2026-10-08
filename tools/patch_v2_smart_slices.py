with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Add sliceFilterMode variable
if "let sliceFilterMode" not in text:
    text = "let sliceFilterMode = 'auto';\n" + text

# 2. Add setSliceFilter function
if "function setSliceFilter" not in text:
    filter_func = """
function setSliceFilter(mode) {
  sliceFilterMode = mode;
  if (activeSlicesData) {
    renderMultiLayerGrid(activeSlicesData);
  }
}
"""
    text = filter_func + "\n" + text

# 3. Update floorSelector in renderMultiLayerGrid
old_floor_selector = """  // 楼层切片选择栏
  const floorSelector = document.getElementById('floorSelector');
  if (floorSelector) {
    if (totalLayers > 1) {
      floorSelector.style.display = 'flex';
      floorSelector.innerHTML = '<span style="font-size:12px; font-weight:700; color:var(--text-3);">立体楼层栈:</span>' + 
        activeLayers.map(fy => {
          const isPlayer = fy === playerFloorY;
          return `<button class="ds-btn ds-btn-sm ${isPlayer ? 'ds-btn-primary' : ''}" onclick="focusFloorSlice(${fy})">[楼层 Y=${fy >= 0 ? '+' + fy : fy} ${isPlayer ? '★当前层' : ''}]</button>`;
        }).join('');
    } else {
      floorSelector.style.display = 'none';
    }
  }"""

new_floor_selector = """  // 楼层切片选择栏
  const floorSelector = document.getElementById('floorSelector');
  if (floorSelector) {
    if (totalLayers > 1) {
      floorSelector.style.display = 'flex';
      const isAuto = (sliceFilterMode === 'auto');
      floorSelector.innerHTML = `
        <span style="font-size:12px; font-weight:700; color:var(--text-3);">立体楼层栈:</span>
        <button class="ds-btn ds-btn-sm ${isAuto ? 'ds-btn-cyan' : 'ds-btn-ghost'}" onclick="setSliceFilter('auto')">[智能涉及层过滤${isAuto ? ' · 开启' : ''}]</button>
        <button class="ds-btn ds-btn-sm ${!isAuto ? 'ds-btn-primary' : 'ds-btn-ghost'}" onclick="setSliceFilter('all')">[显示全部切片]</button>
      ` + activeLayers.map(fy => {
        const isPlayer = fy === playerFloorY;
        return `<button class="ds-btn ds-btn-sm ${isPlayer ? 'ds-btn-green' : ''}" onclick="focusFloorSlice(${fy})">[楼层 Y=${fy >= 0 ? '+' + fy : fy} ${isPlayer ? '★主角层' : ''}]</button>`;
      }).join('');
    } else {
      floorSelector.style.display = 'none';
    }
  }"""
text = text.replace(old_floor_selector, new_floor_selector)

# 4. Smart folding in container.innerHTML = sortedSlices.map(...)
old_slice_render = """    let sliceHtml = `
      <div class="ds-map-slice radius-${currentRadarRadius}" id="slice-floor-${fy}">"""

new_slice_render = """    // 智能切片过滤逻辑：
    // 若当前规划路径未涉及该层、且主角不在此层、且用户未选定该层瓦片，则智能收起为紧凑条目
    const hasPathOnFloor = (pathNodesOnThisSlice > 0);
    const isSelectedFloor = Boolean(currentSelectedCell && currentSelectedCell.y === fy);
    const shouldShowFloor = (sliceFilterMode === 'all') || isPlayerFloor || isSelectedFloor || hasPathOnFloor;

    if (!shouldShowFloor) {
      return `
        <div class="ds-map-slice ds-map-slice-collapsed" style="padding:10px 16px; background:var(--bg-1); border:1px dashed var(--border-subtle); display:flex; justify-content:space-between; align-items:center; border-radius:4px; margin-bottom:8px;">
          <div style="font-size:12px; color:var(--text-3); display:flex; align-items:center; gap:8px;">
            <span class="ds-badge">${fy >= 0 ? '+' + fy : fy} 层标高</span>
            <span style="color:var(--text-1); font-weight:700;">${floorTitle}</span>
            <span style="color:var(--text-3);">（当前规划路径未涉及该层通道，已智能折叠）</span>
          </div>
          <button class="ds-btn ds-btn-sm ds-btn-ghost" onclick="setSliceFilter('all')">[展开查看该层网格]</button>
        </div>
      `;
    }

    let sliceHtml = `
      <div class="ds-map-slice radius-${currentRadarRadius}" id="slice-floor-${fy}">"""
text = text.replace(old_slice_render, new_slice_render)

# 5. Update playerCoordBadge in pollPlayerRuntime
old_poll_runtime_pos = """      const gposPill = document.getElementById('pillGpos');
      if (gposPill) {
        gposPill.innerText = `网格坐标: (${livePlayerGrid.x}, ${livePlayerGrid.z})`;
      }"""

new_poll_runtime_pos = """      const gposPill = document.getElementById('pillGpos');
      if (gposPill) {
        gposPill.innerText = `网格坐标: (${livePlayerGrid.x}, ${livePlayerGrid.z})`;
      }
      const pCoordBadge = document.getElementById('playerCoordBadge');
      if (pCoordBadge) {
        pCoordBadge.innerText = `主角: (X=${livePlayerGrid.x}, Z=${livePlayerGrid.z}, Y=${livePlayerGrid.y ?? 0})`;
      }"""
text = text.replace(old_poll_runtime_pos, new_poll_runtime_pos)

# 6. Update targetCoordBadge in handlePlanNav and handleCellClick
old_plan_nav_start = """  appendLog(`[路径规划] 正在计算前往目标 (${tx}, ${tz}, 层高Y=${ty}) 模式=${mode} 的最优 A* 路径...`);"""

new_plan_nav_start = """  appendLog(`[路径规划] 正在计算前往目标 (${tx}, ${tz}, 层高Y=${ty}) 模式=${mode} 的最优 A* 路径...`);
  const tCoordBadge = document.getElementById('targetCoordBadge');
  if (tCoordBadge) {
    tCoordBadge.innerText = `目标: (X=${tx}, Z=${tz}, Y=${ty})`;
  }"""
text = text.replace(old_plan_nav_start, new_plan_nav_start)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js patched with smart slice folding and coordinates badges.")
