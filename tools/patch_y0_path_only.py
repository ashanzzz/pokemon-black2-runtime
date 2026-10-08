with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

# Replace path badge calculation in renderMultiLayerGrid:
old_badge_section = """    // 统计当前层上的规划路径节点与具体步骤范围 (例如地面 1~5 步，高台独木桥 6~9 步)
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

new_badge_section = """    // 移动指示显色裁决 (用户明确指定：在 UI 层面，移动指示统一在 Y=0 地面 UI 显示，不要显示在 Y=2 中)
    const isGroundSlice = (fy === 0);
    let pathBadge = '';
    if (isGroundSlice && activePlannedPathMap.size > 0) {
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

      const totalNodes = activePlannedPathMap.size;
      pathBadge = `<span class="ds-badge" style="${badgeStyle} font-weight:800;">[全息移动路线 · ${modeZh} · 共 ${totalNodes} 节点]</span>`;
    } else if (fy !== 0) {
      pathBadge = `<span class="ds-badge" style="color:var(--text-3); border:1px solid var(--border-subtle);">[高层立体地形 · 标高 Y=+2]</span>`;
    }"""
text = text.replace(old_badge_section, new_badge_section)

# Replace cell path lookup:
old_cell_path = """        // 路径高亮判定 (步行=明亮绿, 奔跑=深墨绿, 自行车=金黄, 冲浪=纯蓝)
        const pathNode = activePlannedPathMap.get(cellKey) || activePlannedPathMap.get(`${cell.x},${cell.z}`);"""

new_cell_path = """        // 路径高亮判定 (移动指示严格仅在 Y=0 地面切片渲染，绝不渲染在 Y=2 高台切片中)
        const isGroundSlice = (fy === 0);
        const pathNode = isGroundSlice ? (activePlannedPathMap.get(cellKey) || activePlannedPathMap.get(`${cell.x},${cell.z}`)) : null;"""
text = text.replace(old_cell_path, new_cell_path)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js updated: movement indicators strictly confined to Y=0 slice, Y=2 is clean terrain view!")
