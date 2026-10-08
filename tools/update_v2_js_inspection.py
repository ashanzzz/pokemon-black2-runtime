with open("frontend/v2.js", "r", encoding="utf-8") as f:
    code = f.read()

import re

# 1. 替换 renderTileInspection 函数为增强版
old_inspector = re.search(r'// 全息物理感知档案卡渲染[\s\S]*?function renderMultiLayerGrid\(data\) \{', code)

new_inspector = '''// 全息物理感知档案卡渲染 (瓦片明细、材质、载具步态许可矩阵)
function renderTileInspection(cellKey, x, y, z, symbol, walkable, kindName) {
  let cell = activeCellDataMap.get(cellKey);
  
  // 保底查找：若 activeCellDataMap 未命中，从 activeSlicesData 遍历查找
  if (!cell && activeSlicesData && activeSlicesData.slices) {
    for (const s of activeSlicesData.slices) {
      for (const row of (s.grid || [])) {
        for (const c of row) {
          if (c.x === x && c.z === z && c.y === y) {
            cell = c;
            break;
          }
        }
        if (cell) break;
      }
      if (cell) break;
    }
  }
  cell = cell || {};

  const coordElem = document.getElementById('inspectTileCoord');
  if (coordElem) coordElem.innerText = `选定瓦片: (X=${x}, Z=${z})`;

  const layerElem = document.getElementById('inspectTileLayer');
  if (layerElem) layerElem.innerText = `标高 Y=${y >= 0 ? '+' + y : y}`;

  const symElem = document.getElementById('inspectTileSymbol');
  if (symElem) symElem.innerText = `符号: ${symbol}`;

  const statusElem = document.getElementById('inspectTileWalkStatus');
  if (statusElem) {
    if (walkable) {
      statusElem.className = 'ds-badge ds-badge-green';
      statusElem.innerText = '[OK 允许通行]';
    } else {
      statusElem.className = 'ds-badge ds-badge-red';
      statusElem.innerText = '[BLOCK 障碍阻隔]';
    }
  }

  const matElem = document.getElementById('inspectTileMaterial');
  const descElem = document.getElementById('inspectTileSemanticDesc');
  const tileClass = cell.tile_class !== undefined ? cell.tile_class : 0;
  const tileClassHex = cell.tile_class_hex || ('0x' + tileClass.toString(16).toUpperCase().padStart(4, '0'));
  const matInfo = cell.material || {};
  const matKind = matInfo.kind || kindName;
  const matLabel = matInfo.label || kindName;

  if (matElem) {
    matElem.innerText = `材质: ${matKind} · ${matLabel} (TileClass: ${tileClassHex})`;
  }
  if (descElem) {
    descElem.innerText = cell.status || `当前区域物理地块，归属 Zone ${livePlayerZone}。连通性正常，可作为导航与运动目标。`;
  }

  // 全载具与步态许可矩阵 (Walk, Run, Bike, Surf)
  const isWater = (symbol === 'W' || symbol === '~' || matKind.includes('water') || tileClass === 16 || tileClass === 17);
  const isCatwalk = (['╫', '╪', '↕', 'o'].includes(symbol) || matKind.includes('catwalk') || tileClass === 190 || tileClass === 191);
  const isStair = (['▲', '▼'].includes(symbol) || matKind.includes('stair'));

  // 1. 步行 (Walk)
  const mWalk = document.getElementById('matrixWalk');
  if (mWalk) {
    if (walkable && !isWater) {
      mWalk.className = 'ds-badge ds-badge-green';
      mWalk.innerText = '🚶 步行: [OK 允许]';
    } else {
      mWalk.className = 'ds-badge ds-badge-red';
      mWalk.innerText = '🚶 步行: [NO 障碍禁止]';
    }
  }

  // 2. 奔跑 (Run)
  const mRun = document.getElementById('matrixRun');
  if (mRun) {
    if (walkable && !isWater) {
      mRun.className = 'ds-badge ds-badge-green';
      mRun.innerText = '🏃 奔跑: [OK 允许·B键]';
    } else {
      mRun.className = 'ds-badge ds-badge-red';
      mRun.innerText = '🏃 奔跑: [NO 障碍禁止]';
    }
  }

  // 3. 自行车 (Bike - 用户指定黄色)
  const mBike = document.getElementById('matrixBike');
  if (mBike) {
    if (walkable && !isWater && !isCatwalk && !isStair) {
      mBike.className = 'ds-badge ds-badge-yellow';
      mBike.innerText = '🚲 自行车: [OK 极速允许·黄色]';
    } else if (isCatwalk || isStair) {
      mBike.className = 'ds-badge ds-badge-amber';
      mBike.innerText = '🚲 自行车: [NO 狭窄/台阶阻断]';
    } else {
      mBike.className = 'ds-badge ds-badge-red';
      mBike.innerText = '🚲 自行车: [NO 障碍阻断]';
    }
  }

  // 4. 冲浪水路 (Surf - 用户指定蓝色稳妥)
  const mSurf = document.getElementById('matrixSurf');
  if (mSurf) {
    if (isWater) {
      mSurf.className = 'ds-badge ds-badge-blue';
      mSurf.innerText = '🏄 冲浪: [OK 水路航行·蓝色]';
    } else {
      mSurf.className = 'ds-badge';
      mSurf.innerText = '🏄 冲浪: [NO 陆地不可冲浪]';
    }
  }
}

function renderMultiLayerGrid(data) {'''

assert old_inspector, "old_inspector anchor not found"
code = code[:old_inspector.start()] + new_inspector + code[old_inspector.end()-len('function renderMultiLayerGrid(data) {'):]

# 2. 替换 handleCellClick 函数，确保点击第一步就更新档案卡
old_click = re.search(r'// 鼠标点击任意格子[\s\S]*?function handleCellClick\(el\) \{[\s\S]*?\n\}', code)

new_click = '''// 鼠标点击任意格子：拾取坐标、注入寻路台、实时更新瓦片档案卡并计算 A* 路径
function handleCellClick(el) {
  const x = parseInt(el.dataset.x, 10);
  const z = parseInt(el.dataset.z, 10);
  const y = parseInt(el.dataset.y, 10);
  const walkable = (el.dataset.walkable === 'true');
  const sym = el.dataset.symbol || '.';
  const kind = el.dataset.kind || '普通地面';
  const cellKey = `${x},${z},${y}`;

  currentSelectedCell = { x, z, y };

  document.querySelectorAll('.ds-map-cell').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');

  // 1. 立即更新选定瓦片全息物理感知档案卡与载具矩阵
  renderTileInspection(cellKey, x, y, z, sym, walkable, kind);

  // 2. 自动填入寻路输入框
  const xInput = document.getElementById('navTargetX');
  const zInput = document.getElementById('navTargetZ');
  const yInput = document.getElementById('navTargetY');
  if (xInput) { xInput.value = x; xInput.dataset.userEdited = 'true'; }
  if (zInput) { zInput.value = z; zInput.dataset.userEdited = 'true'; }
  if (yInput) { yInput.value = y; yInput.dataset.userEdited = 'true'; }

  const btnNav = document.getElementById('btnQuickNav');
  if (btnNav) btnNav.style.display = walkable ? 'inline-flex' : 'none';

  appendLog(`[地图选点] 拾取坐标 (X=${x}, Z=${z}, Y=${y})，材质: ${kind}，触发 A* 寻路规划...`);
  handlePlanNav();
}'''

assert old_click, "old_click anchor not found"
code = code[:old_click.start()] + new_click + code[old_click.end():]

# 3. 增强 renderMultiLayerGrid 切片头部和路径格子样式
old_slice_render = re.search(r'// 路径高亮判定[\s\S]*?sliceHtml \+= `<div class="ds-map-cell[\s\S]*?onclick="handleCellClick\(this\)">\$\{cellDisplay\}</div>`;', code)

new_slice_render = '''// 路径高亮判定 (步行=明亮绿, 奔跑=深墨绿, 自行车=金黄, 冲浪=纯蓝)
        const pathNode = activePlannedPathMap.get(cellKey);
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
        }

        sliceHtml += `<div class="ds-map-cell ${cellClass} ${pathClass} ${isSelected ? 'selected' : ''}" 
          data-x="${cell.x}" data-z="${cell.z}" data-y="${cell.y}" 
          data-walkable="${cell.walkable}" data-kind="${kindSafe}" 
          data-symbol="${sym}"
          data-cellkey="${cellKey}"
          onmouseenter="handleCellHover(this)" 
          onclick="handleCellClick(this)">${cellDisplay}</div>`;'''

assert old_slice_render, "old_slice_render anchor not found"
code = code[:old_slice_render.start()] + new_slice_render + code[old_slice_render.end():]

# 4. handlePlanNav 成功时点亮连通路径并标出终点
old_path_hook = re.search(r'// 提取路径节点并在网格切片上点亮连通路径[\s\S]*?renderNavPlanDetail\(d\);', code)

new_path_hook = '''// 提取路径节点并在网格切片上点亮连通路径
      activePlannedPathMap.clear();
      activePlannedMovementMode = d.movement?.selected || mode || "run";
      const nodes = d.route_detail?.nodes || d.segments?.[0]?.path || [];
      nodes.forEach((n, idx) => {
        const isGoal = (idx === nodes.length - 1);
        activePlannedPathMap.set(`${n.x},${n.z},${n.y}`, { step: idx + 1, total: nodes.length, isGoal: isGoal });
      });
      if (activeSlicesData) {
        renderMultiLayerGrid(activeSlicesData);
      }

      renderNavPlanDetail(d);'''

assert old_path_hook, "old_path_hook anchor not found"
code = code[:old_path_hook.start()] + new_path_hook + code[old_path_hook.end():]

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(code)

print("Updated frontend/v2.js with tile inspection & vibrant path highlight!")
