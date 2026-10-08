with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Change default sliceFilterMode to 'all'
text = text.replace("let sliceFilterMode = 'auto';", "let sliceFilterMode = 'all';")

# 2. Update floorSelector UI
old_fs_buttons = """        <span style="font-size:12px; font-weight:700; color:var(--text-3);">立体楼层栈:</span>
        <button class="ds-btn ds-btn-sm ${isAuto ? 'ds-btn-cyan' : 'ds-btn-ghost'}" onclick="setSliceFilter('auto')">[智能涉及层过滤${isAuto ? ' · 开启' : ''}]</button>
        <button class="ds-btn ds-btn-sm ${!isAuto ? 'ds-btn-primary' : 'ds-btn-ghost'}" onclick="setSliceFilter('all')">[显示全部切片]</button>
      ` + activeLayers.map(fy => {
        const isPlayer = fy === playerFloorY;
        return `<button class="ds-btn ds-btn-sm ${isPlayer ? 'ds-btn-green' : ''}" onclick="focusFloorSlice(${fy})">[楼层 Y=${fy >= 0 ? '+' + fy : fy} ${isPlayer ? '★主角层' : ''}]</button>`;
      }).join('');"""

new_fs_buttons = """        <span style="font-size:12px; font-weight:700; color:var(--text-3);">立体楼层栈:</span>
        <button class="ds-btn ds-btn-sm ${sliceFilterMode === 'all' ? 'ds-btn-primary' : 'ds-btn-ghost'}" onclick="setSliceFilter('all')">[双层全景对齐 (默认全部展开)]</button>
        <button class="ds-btn ds-btn-sm ${sliceFilterMode === 'active_only' ? 'ds-btn-cyan' : 'ds-btn-ghost'}" onclick="setSliceFilter('active_only')">[仅显示路径涉及层]</button>
      ` + activeLayers.map(fy => {
        const isPlayer = fy === playerFloorY;
        const isFocused = (sliceFilterMode === String(fy));
        return `<button class="ds-btn ds-btn-sm ${isFocused ? 'ds-btn-yellow' : isPlayer ? 'ds-btn-green' : ''}" onclick="setSliceFilter('${fy}')">[仅看 Y=${fy >= 0 ? '+' + fy : fy} ${isPlayer ? '★主角层' : ''}]</button>`;
      }).join('');"""
text = text.replace(old_fs_buttons, new_fs_buttons)

# 3. Update slice filtering logic in renderMultiLayerGrid:
old_filter_logic = """    // 智能切片过滤逻辑：
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
    }"""

new_filter_logic = """    // 切片展示裁决逻辑 (默认 'all' 全景展开，绝不产生“检测到2层却折叠”的矛盾)
    const hasPathOnFloor = (pathNodesOnThisSlice > 0);
    const isSelectedFloor = Boolean(currentSelectedCell && currentSelectedCell.y === fy);
    let shouldShowFloor = true;
    if (sliceFilterMode === 'active_only') {
      shouldShowFloor = isPlayerFloor || isSelectedFloor || hasPathOnFloor;
    } else if (sliceFilterMode !== 'all') {
      // 指定聚焦特定楼层
      shouldShowFloor = (sliceFilterMode === String(fy));
    }

    if (!shouldShowFloor) {
      return `
        <div class="ds-map-slice ds-map-slice-collapsed" style="padding:10px 16px; background:var(--bg-1); border:1px dashed var(--border-subtle); display:flex; justify-content:space-between; align-items:center; border-radius:4px; margin-bottom:8px;">
          <div style="font-size:12px; color:var(--text-3); display:flex; align-items:center; gap:8px;">
            <span class="ds-badge">${fy >= 0 ? '+' + fy : fy} 层标高</span>
            <span style="color:var(--text-1); font-weight:700;">${floorTitle}</span>
            <span style="color:var(--text-3);">（已切换为单层聚焦视图）</span>
          </div>
          <button class="ds-btn ds-btn-sm ds-btn-ghost" onclick="setSliceFilter('all')">[切回双层全景并显]</button>
        </div>
      `;
    }"""
text = text.replace(old_filter_logic, new_filter_logic)

# 4. Enhance path badge on each slice: calculate exact step range for this slice
old_badge_calc = """    // 统计当前层上的规划路径节点数
    let pathNodesOnThisSlice = 0;
    for (const [key, node] of activePlannedPathMap.entries()) {
      if (key.endsWith(`,${fy}`)) {
        pathNodesOnThisSlice++;
      }
    }
    let pathBadge = '';
    if (pathNodesOnThisSlice > 0) {
      if (activePlannedMovementMode === 'walk') {
        pathBadge = `<span class="ds-badge" style="background:#009624; color:#FFFFFF; border:1px solid #00E676; font-weight:800;">[连通路径 · 步行 (亮绿) · ${pathNodesOnThisSlice} 格]</span>`;
      } else if (activePlannedMovementMode === 'bike') {
        pathBadge = `<span class="ds-badge" style="background:#FF8F00; color:#FFFFFF; border:1px solid #FFD54F; font-weight:800;">[连通路径 · 自行车 (金黄) · ${pathNodesOnThisSlice} 格]</span>`;
      } else if (activePlannedMovementMode === 'surf') {
        pathBadge = `<span class="ds-badge" style="background:#0052CC; color:#FFFFFF; border:1px solid #448AFF; font-weight:800;">[连通路径 · 冲浪水路 (纯蓝) · ${pathNodesOnThisSlice} 格]</span>`;
      } else {
        pathBadge = `<span class="ds-badge" style="background:#004D20; color:#69F0AE; border:1px solid #00C853; font-weight:800;">[连通路径 · 连续奔跑 (深绿) · ${pathNodesOnThisSlice} 格]</span>`;
      }
    }"""

new_badge_calc = """    // 统计当前层上的规划路径节点与具体步骤范围 (例如地面 1~5 步，高台独木桥 6~9 步)
    let pathNodesOnThisSlice = 0;
    let sliceSteps = [];
    for (const [key, node] of activePlannedPathMap.entries()) {
      if (key.endsWith(`,${fy}`) && node.step) {
        pathNodesOnThisSlice++;
        sliceSteps.push(node.step);
      }
    }
    sliceSteps.sort((a, b) => a - b);
    let stepRangeStr = '';
    if (sliceSteps.length > 0) {
      stepRangeStr = (sliceSteps[0] === sliceSteps[sliceSteps.length - 1])
        ? `第 ${sliceSteps[0]} 步`
        : `第 ${sliceSteps[0]}~${sliceSteps[sliceSteps.length - 1]} 步`;
    }

    let pathBadge = '';
    if (pathNodesOnThisSlice > 0) {
      const modeZh = {
        'walk': '步行 (亮绿)',
        'bike': '自行车 (金黄)',
        'surf': '冲浪水路 (纯蓝)',
        'run': '连续奔跑 (深绿)'
      }[activePlannedMovementMode] || '连通移动';

      const badgeStyle = {
        'walk': 'background:#009624; color:#FFFFFF; border:1px solid #00E676;',
        'bike': 'background:#FF8F00; color:#FFFFFF; border:1px solid #FFD54F;',
        'surf': 'background:#0052CC; color:#FFFFFF; border:1px solid #448AFF;',
        'run': 'background:#004D20; color:#69F0AE; border:1px solid #00C853;'
      }[activePlannedMovementMode] || 'background:var(--accent-cyan); color:#000;';

      pathBadge = `<span class="ds-badge" style="${badgeStyle} font-weight:800;">[本层路径 · ${modeZh} · ${stepRangeStr} (共 ${pathNodesOnThisSlice} 格)]</span>`;
    } else if (activePlannedPathMap.size > 0) {
      pathBadge = `<span class="ds-badge" style="color:var(--text-3); border:1px solid var(--border-subtle);">[本层未涉入 · 空间通道畅通]</span>`;
    }"""
text = text.replace(old_badge_calc, new_badge_calc)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js updated: full multi-layer parallel view is default, no more contradiction!")
