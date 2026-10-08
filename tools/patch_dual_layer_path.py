with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Update pathNode determination in renderMultiLayerGrid:
old_slice_path = """        // 路径高亮判定 (移动指示严格仅在 Y=0 地面切片渲染，绝不渲染在 Y=2 高台切片中)
        const isGroundSlice = (fy === 0);
        const pathNode = isGroundSlice ? (activePlannedPathMap.get(cellKey) || activePlannedPathMap.get(`${cell.x},${cell.z}`)) : null;
        let pathClass = '';
        let cellDisplay = sym;
        if (pathNode) {
          if (activePlannedMovementMode === 'walk') {
            pathClass = 'cell-path-walk';
          } else if (activePlannedMovementMode === 'bike') {
            pathClass = 'cell-path-bike';
          } else if (activePlannedMovementMode === 'surf') {
            pathClass = 'cell-path-surf';
          } else {
            pathClass = 'cell-path-run'; // 默认奔跑：深墨绿
          }
          if (pathNode.isGoal) {
            pathClass += ' cell-path-goal';
            cellDisplay = '★';
          } else if (!cell.is_player && sym !== 'P') {
            cellDisplay = pathNode.step;
          }
        }"""

new_slice_path = """        // 立体双模路径高亮裁决：
        // 1. 本层真实实体走过的路径 (exactPathNode): 实心高亮正常显示 (在Y=2实际走的地图上，5,6,7,8和星星★都正常高亮显示！)
        // 2. 跨层垂直投影 (projPathNode): 边缘发光虚线框 (在Y=0显示5,6,7,8和终点★边缘发光，提示同坐标在另一层)
        const exactPathNode = activePlannedPathMap.get(cellKey);
        const projPathNode = activePlannedPathMap.get(`${cell.x},${cell.z}`);
        let pathClass = '';
        let cellDisplay = sym;

        if (exactPathNode) {
          // 本层真实实体路径 (实心高亮正常呈现，终点★)
          if (activePlannedMovementMode === 'walk') {
            pathClass = 'cell-path-walk';
          } else if (activePlannedMovementMode === 'bike') {
            pathClass = 'cell-path-bike';
          } else if (activePlannedMovementMode === 'surf') {
            pathClass = 'cell-path-surf';
          } else {
            pathClass = 'cell-path-run'; // 默认奔跑：深墨绿
          }
          if (exactPathNode.isGoal) {
            pathClass += ' cell-path-goal';
            cellDisplay = '★';
          } else if (!cell.is_player && sym !== 'P') {
            cellDisplay = exactPathNode.step;
          }
        } else if (projPathNode) {
          // 跨层垂直投影节点 (边缘发光，代表不是这一层，但处于同坐标上方或下方)
          pathClass = `cell-path-projected proj-${activePlannedMovementMode}`;
          if (projPathNode.isGoal) {
            pathClass += ' proj-goal';
            cellDisplay = '★';
          } else if (!cell.is_player && sym !== 'P') {
            cellDisplay = projPathNode.step;
          }
        }"""
text = text.replace(old_slice_path, new_slice_path)

# 2. Update pathBadge section in renderMultiLayerGrid:
old_badge_calc = """    // 移动指示显色裁决 (用户明确指定：在 UI 层面，移动指示统一在 Y=0 地面 UI 显示，不要显示在 Y=2 中)
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

new_badge_calc = """    // 统计当前切片上的真实实体步骤与跨层发光投影步骤
    let realSteps = [];
    let projSteps = [];
    for (const [key, node] of activePlannedPathMap.entries()) {
      if (node.isRealFloor && key.endsWith(`,${fy}`) && node.step) {
        realSteps.push(node.step);
      } else if (node.isProjection && key.split(',').length === 2 && node.step) {
        if (node.y !== fy) {
          projSteps.push(node.step);
        }
      }
    }
    realSteps = Array.from(new Set(realSteps)).sort((a, b) => a - b);
    projSteps = Array.from(new Set(projSteps)).sort((a, b) => a - b);

    let pathBadge = '';
    if (realSteps.length > 0 || projSteps.length > 0) {
      let badges = [];
      if (realSteps.length > 0) {
        const rStr = (realSteps[0] === realSteps[realSteps.length - 1]) ? `第 ${realSteps[0]} 步` : `第 ${realSteps[0]}~${realSteps[realSteps.length - 1]} 步`;
        badges.push(`<span class="ds-badge" style="background:#FF8F00; color:#000; font-weight:800;">[本层实体: ${rStr}]</span>`);
      }
      if (projSteps.length > 0) {
        const pStr = (projSteps[0] === projSteps[projSteps.length - 1]) ? `第 ${projSteps[0]} 步` : `第 ${projSteps[0]}~${projSteps[projSteps.length - 1]} 步`;
        badges.push(`<span class="ds-badge" style="background:rgba(255,214,0,0.15); border:1px dashed #FFD600; color:#FFE082; font-weight:700;">[跨层发光投影: ${pStr}]</span>`);
      }
      pathBadge = badges.join(' ');
    } else if (fy !== 0) {
      pathBadge = `<span class="ds-badge" style="color:var(--text-3); border:1px solid var(--border-subtle);">[高层立体地形 · 标高 Y=+2]</span>`;
    }"""
text = text.replace(old_badge_calc, new_badge_calc)

# 3. Update handlePlanNav nodes.forEach:
old_set_nodes = """      nodes.forEach((n, idx) => {
        const isGoal = (idx === nodes.length - 1);
        const nodeY = (n.y !== undefined && n.y !== null) ? n.y : ty;
        activePlannedPathMap.set(`${n.x},${n.z},${nodeY}`, { step: idx + 1, total: nodes.length, isGoal: isGoal });
        activePlannedPathMap.set(`${n.x},${n.z}`, { step: idx + 1, total: nodes.length, isGoal: isGoal });
      });"""

new_set_nodes = """      nodes.forEach((n, idx) => {
        const isGoal = (idx === nodes.length - 1);
        const nodeY = (n.y !== undefined && n.y !== null) ? n.y : ty;
        activePlannedPathMap.set(`${n.x},${n.z},${nodeY}`, { step: idx + 1, total: nodes.length, isGoal: isGoal, y: nodeY, isRealFloor: true });
        activePlannedPathMap.set(`${n.x},${n.z}`, { step: idx + 1, total: nodes.length, isGoal: isGoal, y: nodeY, isProjection: true });
      });"""
text = text.replace(old_set_nodes, new_set_nodes)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js updated with dual-layer path logic (solid on real floor, glowing dashed projection on other floor)!")
