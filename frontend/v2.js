
function appendNavTaskLog(msg, color) {
  const box = document.getElementById('navLiveTaskLog');
  if (!box) return;
  const time = new Date().toTimeString().split(' ')[0];
  const colorStyle = color ? `style="color:${color}; font-weight:700;"` : '';
  box.innerHTML += `<div ${colorStyle}>[${time}] ${msg}</div>`;
  box.scrollTop = box.scrollHeight;
}


function setSliceFilter(mode) {
  sliceFilterMode = mode;
  if (activeSlicesData) {
    renderMultiLayerGrid(activeSlicesData);
  }
}

let sliceFilterMode = 'all';
/**
 * 黑2自主控制台 V2 客户端测试引擎
 * 纯原生驱动 · 零外部依赖 · 严格 0 Emoji · 全中文排版与日志
 */

let livePlayerZone = 457;
let livePlayerGrid = { x: 20, y: 0, z: 30 };
let currentRadarRadius = 4;
let activePlannedPathMap = new Map();
let activePlannedMovementMode = "run";
let activeCellDataMap = new Map();
let activeSlicesData = null;
let currentSelectedCell = null;
let is3DActive = false;
let simulatedBattleMode = false;

// 切换左侧活动导轨选项卡 (离开 3D 时自动休眠 WebGL)
function switchTab(paneId) {
  document.querySelectorAll('.ds-rail-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.ds-pane').forEach(p => p.classList.remove('active'));
  
  const targetBtn = Array.from(document.querySelectorAll('.ds-rail-btn')).find(b => b.getAttribute('onclick')?.includes(paneId));
  if (targetBtn) targetBtn.classList.add('active');
  
  const targetPane = document.getElementById(paneId);
  if (targetPane) targetPane.classList.add('active');

  // 同步 URL hash
  const hashKey = paneId.replace('pane-', '');
  if (window.location.hash !== "#" + hashKey) {
    history.replaceState(null, '', '#' + hashKey);
  }

  // 如果离开 3D 页面，自动挂起休眠释放 GPU
  if (paneId !== 'pane-3d' && is3DActive) {
    pause3DView();
  }
  // 切换到空间地图时，立即重绘或拉取最新雷达切片
  if (paneId === 'pane-radar') {
    if (activeSlicesData) {
      renderMultiLayerGrid(activeSlicesData);
    }
    pollRadar();
  }
}

// 终端控制台追加日志
let isLogDrawerOpen = false;

// 全局底部实时日志控制台抽屉控制 (全站任意 Tab 随时展开/收起)
function toggleGlobalLogDrawer() {
  const drawer = document.getElementById('globalLogDrawer');
  const btn = document.getElementById('btnToggleLog');
  if (!drawer) return;
  isLogDrawerOpen = !isLogDrawerOpen;
  if (isLogDrawerOpen) {
    drawer.style.display = 'flex';
    if (btn) {
      btn.innerText = '[收起日志终端]';
      btn.className = 'ds-btn ds-btn-sm ds-btn-danger';
    }
    const box = document.getElementById('consoleLog');
    if (box) box.scrollTop = box.scrollHeight;
  } else {
    drawer.style.display = 'none';
    if (btn) {
      btn.innerText = '[打开实时日志终端]';
      btn.className = 'ds-btn ds-btn-sm ds-btn-cyan';
    }
  }
}

function clearConsoleLog() {
  const box = document.getElementById('consoleLog');
  if (box) box.innerText = '';
}

function appendLog(msg) {
  const box = document.getElementById('consoleLog');
  if (!box) return;
  const time = new Date().toTimeString().split(' ')[0];
  box.innerText += `\n[${time}] ${msg}`;
  box.scrollTop = box.scrollHeight;
}

// 轮询玩家实时物理状态与坐标自愈
async function pollPlayerRuntime() {
  try {
    const res = await fetch('/api/v1/player/runtime');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (data.position?.grid) {
      const prevX = livePlayerGrid.x;
      const prevZ = livePlayerGrid.z;
      livePlayerGrid = data.position.grid;

      const gposPill = document.getElementById('pillGpos');
      if (gposPill) {
        gposPill.innerText = `网格坐标: (${livePlayerGrid.x}, ${livePlayerGrid.z})`;
      }
      const pCoordBadge = document.getElementById('playerCoordBadge');
      if (pCoordBadge) {
        pCoordBadge.innerText = `主角: (X=${livePlayerGrid.x}, Z=${livePlayerGrid.z}, Y=${livePlayerGrid.y ?? 0})`;
      }
      const navStart = document.getElementById('navStartCoord');
      if (navStart && (!navStart.innerText || navStart.innerText.includes('等待读取'))) {
        navStart.innerText = `X=${livePlayerGrid.x}, Y=${livePlayerGrid.y ?? 0}, Z=${livePlayerGrid.z}`;
      }

      // 如果寻路输入框处于默认值，自动同步为主角当前前方坐标
      const xInput = document.getElementById('navTargetX');
      const zInput = document.getElementById('navTargetZ');
      const yInput = document.getElementById('navTargetY');
      if (xInput && (xInput.value === '22' || xInput.value === '23')) {
        xInput.value = livePlayerGrid.x;
      }
      if (zInput && (zInput.value === '45' || zInput.value === '675')) {
        zInput.value = livePlayerGrid.z >= 2 ? livePlayerGrid.z - 2 : livePlayerGrid.z + 2;
      }
      if (yInput && (yInput.value === '' || yInput.value === '0')) {
        yInput.value = livePlayerGrid.y ?? 0;
      }
    }

    if (data.zone_id) {
      livePlayerZone = data.zone_id;
      const zonePill = document.getElementById('pillZone');
      if (zonePill) {
        zonePill.innerText = `当前区域: ${data.zone_id} 立涌工业园区`;
      }
    }

    const bridgeBadge = document.getElementById('badgeBridge');
    if (bridgeBadge) {
      bridgeBadge.className = 'ds-badge ds-badge-green';
      bridgeBadge.innerHTML = '<span class="ds-dot"></span>模拟器网桥: 正常连通';
    }
  } catch (err) {
    const bridgeBadge = document.getElementById('badgeBridge');
    if (bridgeBadge) {
      bridgeBadge.className = 'ds-badge ds-badge-amber';
      bridgeBadge.innerHTML = '<span class="ds-dot"></span>模拟器网桥: 轮询中...';
    }
  }
}

// 轮询并渲染 2D 空间多层对齐网格地图 (容错骨架，绝不空白)

// 切换雷达视口范围 (9x9, 15x15, 21x21, 31x31)
function handleRadiusChange(val) {
  currentRadarRadius = parseInt(val, 10) || 4;
  appendLog(`[雷达视口] 视口范围已切换为半径 ${currentRadarRadius} (${currentRadarRadius * 2 + 1}x${currentRadarRadius * 2 + 1} 空间网格)...`);
  pollRadar();
}

async function pollRadar() {
  const loading = document.getElementById('mapSliceLoading');
  try {
    const res = await fetch(`/api/v1/navigation/radar/slices?radius=${currentRadarRadius}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    activeSlicesData = data;
    if (loading) loading.style.display = 'none';
    renderMultiLayerGrid(data);

    // 若用户尚未手动点击选点，默认自动将瓦片档案卡对齐到主角当前站位瓦片
    if (!currentSelectedCell && livePlayerGrid) {
      const pKey = `${livePlayerGrid.x},${livePlayerGrid.z},${livePlayerGrid.y ?? 0}`;
      const pCell = activeCellDataMap.get(pKey);
      if (pCell) {
        renderTileInspection(pKey, livePlayerGrid.x, livePlayerGrid.y ?? 0, livePlayerGrid.z, 'P', pCell.walkable, pCell.kind || '主角站位');
      }
    }
  } catch (e) {
    if (loading) {
      loading.innerHTML = `<span class="ds-badge ds-badge-amber">[地图切片读取提示: ${e.message}]</span> <button class="ds-btn ds-btn-sm" onclick="pollRadar()">[点击重试]</button>`;
    }
  }
}

// 动态渲染多层切片网格与 X/Z 双轴标尺

// 全息物理感知档案卡渲染 (瓦片明细、材质、载具步态许可矩阵)
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

  const matElem = document.getElementById('inspectTileMaterial');
  const descElem = document.getElementById('inspectTileSemanticDesc');
  const tileClass = cell.tile_class !== undefined ? cell.tile_class : 0;
  const tileClassHex = cell.tile_class_hex || ('0x' + tileClass.toString(16).toUpperCase().padStart(4, '0'));
  const matInfo = cell.material || {};
  const matKind = matInfo.kind || kindName;
  const matLabel = matInfo.label || kindName;

  const hasWarp = Boolean(cell.has_warp || symbol === 'D' || cell.underlying_symbol === 'D');
  const isPlayerStandingHere = (x === livePlayerGrid.x && z === livePlayerGrid.z && (y === livePlayerGrid.y || livePlayerGrid.y === undefined));

  const altY = (cell.alternate_layer_y !== undefined && cell.alternate_layer_y !== null) ? cell.alternate_layer_y : (y > 0 ? 0 : 2);
  const isAltHigher = (altY > y); // 另一层在上方 (如当前 y=0, 上方 altY=2) -> 高台支架 / 实体墙基
  const isAltLower = (altY < y);  // 另一层在下方 (如当前 y=2, 下方 altY=0) -> 高空敞空 / 悬空空间

  const isPlatformBase = (!walkable && cell.alternate_layer_available && isAltHigher);
  const isAirGap = (!walkable && (symbol === '↕' || cell.alternate_layer_available) && isAltLower);
  const isCatwalkEntry = (symbol === '╪' || tileClass === 191 || matKind === 'catwalk_entry');
  const isCatwalkBody = (symbol === '╫' || tileClass === 190 || matKind === 'catwalk');
  const isStair = (symbol === '▲' || symbol === '▼' || matKind.includes('stair') || String(cell.kind || '').includes('slope') || String(cell.kind || '').includes('阶梯'));
  const isStoryTrigger = Boolean(symbol === '!' || cell.is_story_gate_trigger || String(cell.kind || '').includes('剧情拦截') || String(cell.kind || '').includes('触发线'));
  const isNpcOccupied = Boolean((symbol === 'N' || cell.is_npc_blocked || String(cell.kind || '').includes('NPC')) && !isPlayerStandingHere);

  const statusElem = document.getElementById('inspectTileWalkStatus');
  if (statusElem) {
    if (isStoryTrigger) {
      statusElem.className = 'ds-badge ds-badge-red';
      statusElem.innerText = '[BLOCK 剧情拦截阻隔]';
    } else if (isNpcOccupied) {
      statusElem.className = 'ds-badge ds-badge-red';
      statusElem.innerText = '[BLOCK NPC实体阻隔]';
    } else if (walkable) {
      statusElem.className = 'ds-badge ds-badge-green';
      statusElem.innerText = '[OK 允许通行]';
    } else {
      statusElem.className = 'ds-badge ds-badge-red';
      statusElem.innerText = '[BLOCK 障碍阻隔]';
    }
  }

  if (matElem) {
    if (isPlatformBase) {
      matElem.innerHTML = `<strong style="color:var(--accent-red);">[🧱 高台支架 / 实体墙基]</strong> 材质: platform_support · 高架支撑立柱/桥墩 (上方 Y=+${altY} 为高台平台)`;
    } else if (isAirGap) {
      matElem.innerHTML = `<strong style="color:var(--text-3);">[☁️ 空中敞空 / 悬空空间]</strong> 材质: open_air · 无上层路面 (空中悬空)`;
    } else if (isCatwalkEntry) {
      matElem.innerHTML = `<strong style="color:var(--accent-amber);">[╪ 独木桥入口]</strong> 材质: catwalk_entry · 独木桥桥头入口 (TileClass: ${tileClassHex})`;
    } else if (isCatwalkBody) {
      matElem.innerHTML = `<strong style="color:var(--accent-amber);">[╫ 独木桥主体]</strong> 材质: catwalk · 独木桥主体 (TileClass: ${tileClassHex})`;
    } else if (isStair) {
      const isUp = (y <= 0);
      const stairSym = isUp ? '▲' : '▼';
      const stairLabel = isUp ? '上行爬升阶梯' : '下行降落阶梯';
      matElem.innerHTML = `<strong style="color:var(--accent-cyan);">[${stairSym} ${stairLabel}]</strong> 材质: staircase_slope · 跨层立体阶梯 (TileClass: ${tileClassHex})`;
    } else if (isStoryTrigger) {
      const trig = cell.story_gate_trigger || {};
      const scrid = trig.script_id || 'Trigger';
      matElem.innerHTML = `<strong style="color:var(--accent-red);">[⛔ 剧情拦截线]</strong> 材质: story_trigger · 剧情强制截停触发区 (脚本 #${scrid})`;
    } else if (isNpcOccupied) {
      matElem.innerHTML = `<strong style="color:var(--accent-amber);">[N 场景角色占位]</strong> 材质: npc_actor · 动态角色实体占位阻挡`;
    } else if (hasWarp) {
      matElem.innerHTML = `<strong style="color:var(--accent-red);">[D 传送大门]</strong> 材质: ${matKind} · 跨区传送门垫 (TileClass: ${tileClassHex})`;
    } else if (isPlayerStandingHere) {
      matElem.innerHTML = `<strong style="color:var(--accent-cyan);">[P 主角站立点]</strong> 材质: ${matKind} · ${matLabel} (TileClass: ${tileClassHex})`;
    } else {
      matElem.innerText = `材质: ${matKind} · ${matLabel} (TileClass: ${tileClassHex})`;
    }
  }

  if (descElem) {
    if (isPlatformBase) {
      descElem.innerHTML = `<span style="color:var(--text-2);">🧱 标高 Y=${y >= 0 ? '+' + y : y} 处为上方高台的<strong>实体支撑立柱/墙基支架（不可穿越）</strong>。其正上方标高 Y=+${altY} 处为可通行高台平台（<strong>${matKind} · ${matLabel}</strong>）。在当前地面层，此处为阻隔墙体；如需登上高台，请经由旁边的阶梯走廊爬升。</span>`;
    } else if (isAirGap) {
      descElem.innerHTML = `<span style="color:var(--text-2);">☁️ 标高 Y=${y >= 0 ? '+' + y : y} 处为高空敞空区（无高架或独木桥支撑，无法立足或随意跳下）。下方地面投影为: <strong>${matKind} · ${matLabel} (标高 Y=${altY >= 0 ? '+' + altY : altY})</strong>。如需下行，请通过阶梯通道返回地面。</span>`;
    } else if (isCatwalkEntry) {
      descElem.innerHTML = `<span style="color:var(--accent-amber); font-weight:700;">🌉 独木桥桥头入口 (Catwalk Entry · 0x00BF)：</span>高台平坦路面与独木桥的平整接驳端点。经黑2实机验证：游戏引擎在踏入此格时即严禁自行车驶入，进入前必须下车换乘步行或奔跑！主轴向可通行，垂直侧向为悬空边缘。`;
    } else if (isCatwalkBody) {
      descElem.innerHTML = `<span style="color:var(--accent-amber); font-weight:700;">🌉 独木桥主体 (Catwalk · 0x00BE)：</span>高空狭窄木桥，仅限主轴方向通行，南北两侧为高空坠落边缘。支持步行与跑步平衡通过，自行车严禁驶入。`;
    } else if (isStair) {
      const isUp = (y <= 0);
      descElem.innerHTML = isUp
        ? `<span style="color:var(--accent-cyan); font-weight:700;">⛰️ 跨层上行阶梯通道 (▲)：</span>连接地面 Y=0 ➔ 高台 Y=+2。在此处向西直行即可安全爬升至高台平台 (X=10, Y=2)。南北两侧带有防护栏，无法侧向掉落。`
        : `<span style="color:var(--accent-amber); font-weight:700;">⛰️ 跨层下行阶梯通道 (▼)：</span>连接高台 Y=+2 ➔ 地面 Y=0。在此处向东直行即可安全走下阶梯返回地面道路。南北两侧带有防护栏。`;
    } else if (isStoryTrigger) {
      const trig = cell.story_gate_trigger || {};
      const cond = trig.condition || '当前主线剧情未完成';
      const clue = trig.dialogue_clue || '踩入后将强制被 NPC 截停并推回';
      descElem.innerHTML = `<span style="color:var(--accent-red); font-weight:700;">⛔ 剧情拦截触发线：</span>此格被游戏主线剧情设置为截停防线！踏入此格将直接触发剧情对话并强行退回原位。<br><span style="color:var(--accent-amber); font-weight:700;">【解锁条件】${cond}</span><br><span style="color:var(--text-3); font-size:11px;">线索: ${clue}</span>`;
    } else if (isNpcOccupied) {
      descElem.innerHTML = `<span style="color:var(--accent-amber); font-weight:700;">⛔ 场景 NPC 实体占位阻挡：</span>该格当前有 NPC 角色站立，空间物理不可重叠，无法直接站立或穿透。请从两侧绕行或上前对话。`;
    } else if (hasWarp && isPlayerStandingHere) {
      descElem.innerHTML = `<span style="color:var(--accent-red); font-weight:700;">🚪 主角当前正站在跨区传送门垫 [D] 上！踏入或向此方向移动将触发地图黑屏转场。</span>`;
    } else if (hasWarp) {
      descElem.innerHTML = `<span style="color:var(--accent-red); font-weight:700;">🚪 传送大门出入口 [D]：ROM 预设跨区传送门垫。踏入将直接传送至外部相邻地图。</span>`;
    } else if (isPlayerStandingHere) {
      const northKey = `${x},${z - 1},${y}`;
      const isNorthDoor = (activeCellDataMap.get(northKey)?.symbol === 'D' || activeCellDataMap.get(northKey)?.has_warp);
      if (isNorthDoor) {
        descElem.innerHTML = `<span style="color:var(--accent-amber); font-weight:700;">🚪 门前待命格 (Doorstep)：主角当前站立于大门入口前 1 格。正前方 (X=${x}, Z=${z - 1}) 即为传送门 [D]！</span>`;
      } else {
        descElem.innerText = cell.status || `主角当前站立地块，归属 Zone ${livePlayerZone}。连通性正常。`;
      }
    } else {
      descElem.innerText = cell.status || `当前区域物理地块，归属 Zone ${livePlayerZone}。连通性正常，可作为导航与运动目标。`;
    }
  }

  // 全载具与步态许可矩阵 (Walk, Run, Bike, Surf)
  const isWater = (symbol === 'W' || symbol === '~' || matKind.includes('water') || tileClass === 16 || tileClass === 17);
  const isTrueCatwalk = (symbol === '╫' || symbol === '╪' || matKind.includes('catwalk') || tileClass === 190 || tileClass === 191);
  
  // 1. 步行 (Walk)
  const mWalk = document.getElementById('matrixWalk');
  if (mWalk) {
    if (isPlatformBase) {
      mWalk.className = 'ds-badge ds-badge-red';
      mWalk.innerText = '🚶 步行: [NO 支架实体阻隔]';
    } else if (isAirGap) {
      mWalk.className = 'ds-badge ds-badge-red';
      mWalk.innerText = '🚶 步行: [NO 悬空无法立足]';
    } else if (walkable && !isWater) {
      mWalk.className = 'ds-badge ds-badge-green';
      mWalk.innerText = isTrueCatwalk ? '🚶 步行: [OK 允许·独木桥平衡]' : '🚶 步行: [OK 允许]';
    } else {
      mWalk.className = 'ds-badge ds-badge-red';
      mWalk.innerText = '🚶 步行: [NO 障碍禁止]';
    }
  }

  // 2. 奔跑 (Run)
  const mRun = document.getElementById('matrixRun');
  if (mRun) {
    if (isPlatformBase) {
      mRun.className = 'ds-badge ds-badge-red';
      mRun.innerText = '🏃 奔跑: [NO 支架实体阻隔]';
    } else if (isAirGap) {
      mRun.className = 'ds-badge ds-badge-red';
      mRun.innerText = '🏃 奔跑: [NO 悬空无法奔跑]';
    } else if (walkable && !isWater) {
      mRun.className = 'ds-badge ds-badge-green';
      mRun.innerText = isTrueCatwalk ? '🏃 奔跑: [OK 允许·快速过桥]' : '🏃 奔跑: [OK 允许·B键]';
    } else {
      mRun.className = 'ds-badge ds-badge-red';
      mRun.innerText = '🏃 奔跑: [NO 障碍禁止]';
    }
  }

  // 3. 自行车 (Bike - 用户指定黄色)
  const mBike = document.getElementById('matrixBike');
  if (mBike) {
    if (isPlatformBase) {
      mBike.className = 'ds-badge ds-badge-red';
      mBike.innerText = '🚲 自行车: [NO 支架实体阻隔]';
    } else if (isAirGap) {
      mBike.className = 'ds-badge ds-badge-red';
      mBike.innerText = '🚲 自行车: [NO 悬空无法骑行]';
    } else if (isCatwalkEntry) {
      mBike.className = 'ds-badge ds-badge-amber';
      mBike.innerText = '🚲 自行车: [NO 独木桥入口禁行 (需下车)]';
    } else if (isCatwalkBody) {
      mBike.className = 'ds-badge ds-badge-amber';
      mBike.innerText = '🚲 自行车: [NO 独木桥狭窄阻断 (自动下车)]';
    } else if (isStair) {
      mBike.className = 'ds-badge ds-badge-amber';
      mBike.innerText = '🚲 自行车: [NO 阶梯阻断]';
    } else if (walkable && !isWater) {
      mBike.className = 'ds-badge ds-badge-yellow';
      mBike.innerText = '🚲 自行车: [OK 极速允许·黄色]';
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
      mSurf.innerText = isAirGap ? '🏄 冲浪: [NO 悬空非水面]' : '🏄 冲浪: [NO 陆地不可冲浪]';
    }
  }
}

function renderMultiLayerGrid(data) {
  const container = document.getElementById('mapSliceContainer');
  if (!container) return;

  const totalLayers = data.total_active_layers || 1;
  const activeLayers = data.active_layers || [0];
  const playerFloorY = data.player_floor_y ?? 0;
  const slices = data.slices || [];

  if (slices.length === 0) {
    container.innerHTML = '<div style="padding:20px; color:var(--text-3); text-align:center;">当前区域无切片数据</div>';
    return;
  }

  // 更新顶栏多层提示
  const summaryBadge = document.getElementById('layerSummaryBadge');
  const stair = data.stair_runtime || {};
  if (summaryBadge) {
    if (stair.active) {
      summaryBadge.innerText = `检测到 2 层立体标高通道 (地面 Y=0 vs 高架 Y=+2) + 中间双阶楼梯 (主角踩踏第 ${stair.current_step} 阶 / 共 2 阶 · 标高 ${stair.step_world_y})`;
    } else if (totalLayers === 1) {
      summaryBadge.innerText = `当前区域为单层地面 (标高 Y=${activeLayers[0]})`;
    } else if (totalLayers === 2) {
      summaryBadge.innerText = `检测到 2 层立体标高通道 (地面 Y=0 vs 高架 Y=+2)`;
    } else {
      summaryBadge.innerText = `检测到 ${totalLayers} 层立体楼宇/阶梯通道 (共 ${totalLayers} 层)`;
    }
  }

  // 楼层切片选择栏
  const floorSelector = document.getElementById('floorSelector');
  if (floorSelector) {
    if (totalLayers > 1) {
      floorSelector.style.display = 'flex';
      const isAuto = (sliceFilterMode === 'auto');
      floorSelector.innerHTML = `
        <span style="font-size:12px; font-weight:700; color:var(--text-3);">立体楼层栈:</span>
        <button class="ds-btn ds-btn-sm ${sliceFilterMode === 'all' ? 'ds-btn-primary' : 'ds-btn-ghost'}" onclick="setSliceFilter('all')">[双层全景对齐 (默认全部展开)]</button>
        <button class="ds-btn ds-btn-sm ${sliceFilterMode === 'active_only' ? 'ds-btn-cyan' : 'ds-btn-ghost'}" onclick="setSliceFilter('active_only')">[仅显示路径涉及层]</button>
      ` + activeLayers.map(fy => {
        const isPlayer = fy === playerFloorY;
        const isFocused = (sliceFilterMode === String(fy));
        return `<button class="ds-btn ds-btn-sm ${isFocused ? 'ds-btn-yellow' : isPlayer ? 'ds-btn-green' : ''}" onclick="setSliceFilter('${fy}')">[仅看 Y=${fy >= 0 ? '+' + fy : fy} ${isPlayer ? '★主角层' : ''}]</button>`;
      }).join('');
    } else {
      floorSelector.style.display = 'none';
    }
  }

  // 按标高从小到大严格升序排序：Floor Y=0 (下层地面) -> Y=1 (楼梯) -> Y=2 (上层高台)
  const sortedSlices = [...slices].sort((a, b) => (a.floor_y ?? 0) - (b.floor_y ?? 0));

  container.innerHTML = sortedSlices.map(s => {
    const fy = s.floor_y ?? 0;
    const isPlayerFloor = (fy === playerFloorY);
    const grid = s.grid || [];
    if (grid.length === 0) return '';

    const minX = grid[0][0].x;
    const maxX = grid[0][grid[0].length - 1].x;

    const stair = data.stair_runtime || {};
    let floorTitle = `【${fy >= 0 ? '+' + fy : fy} 层标高切片】`;
    if (stair.active && fy === stair.stair_slice_y) {
      floorTitle = `【楼梯跨层通道 标高 Y=+1 ★主角踩踏第 ${stair.current_step} 阶 (标高 ${stair.step_world_y})】`;
    } else if (fy === 0) {
      floorTitle = `【下层地面切片 标高 Y=0 ${isPlayerFloor && !stair.active ? '★主角当前所在层' : ''}】`;
    } else if (fy === 1) {
      floorTitle = `【楼梯跨层中段 标高 Y=+1 ${isPlayerFloor ? '★主角当前所在位置' : ''}】`;
    } else if (fy === 2) {
      floorTitle = `【上层高台切片 标高 Y=+2 ${isPlayerFloor && !stair.active ? '★主角当前所在层' : ''}】`;
    } else {
      floorTitle = `【${fy >= 0 ? '+' + fy : fy} 层标高切片 ${isPlayerFloor ? '★主角当前所在层' : ''}】`;
    }

    // 统计当前切片上的真实实体步数与跨层发光投影步数
    const currentFloorCoords = new Set();
    grid.forEach(row => row.forEach(c => {
      if (c && c.walkable) currentFloorCoords.add(`${c.x},${c.z}`);
    }));
    let realSteps = [];
    let projSteps = [];
    for (const [key, node] of activePlannedPathMap.entries()) {
      if (node.isRealFloor && key.endsWith(`,${fy}`) && node.step) {
        realSteps.push(node.step);
      } else if (node.isProjection && key.split(",").length === 2 && node.step) {
        const parts = key.split(",");
        if (node.y !== fy && currentFloorCoords.has(`${parts[0]},${parts[1]}`)) {
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
    }

    // 切片展示裁决逻辑 (默认 'all' 全景展开，绝不产生“检测到2层却折叠”的矛盾)
    const hasPathOnFloor = (realSteps.length > 0);
    const isSelectedFloor = Boolean(currentSelectedCell && currentSelectedCell.y === fy);
    let shouldShowFloor = true;
    if (sliceFilterMode === 'active_only') {
      shouldShowFloor = isPlayerFloor || isSelectedFloor || hasPathOnFloor;
    } else if (sliceFilterMode === 'all') {
      if (activeSlicesData && activeSlicesData.slices && activeSlicesData.slices.length > 2) {
        shouldShowFloor = isPlayerFloor || isSelectedFloor || hasPathOnFloor;
      } else {
        shouldShowFloor = true;
      }
    } else {
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
    }

    let sliceHtml = `
      <div class="ds-map-slice radius-${currentRadarRadius}" id="slice-floor-${fy}">
        <div class="ds-map-slice-header">
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="color:${isPlayerFloor ? 'var(--accent-cyan)' : 'var(--text-1)'}; font-family:var(--font-mono); font-size:14px; font-weight:700;">
              ${floorTitle}
            </span>
            ${pathBadge}
          </div>
          <span class="ds-badge ${isPlayerFloor ? 'ds-badge-cyan' : ''}">层高 Y=${fy}</span>
        </div>
        <div class="ds-map-grid-body">
          <div class="ds-map-ruler-row">
            <div class="ds-ruler-corner">Z\\X</div>`;

    for (let x = minX; x <= maxX; x++) {
      sliceHtml += `<div class="ds-ruler-col">${x}</div>`;
    }
    sliceHtml += `</div>`;

    grid.forEach(row => {
      const z = row[0].z;
      sliceHtml += `<div class="ds-map-row"><div class="ds-ruler-row">${z}</div>`;
      row.forEach(cell => {
        const rawSym = cell.symbol || '.';
        let sym = rawSym;
        let cellClass = 'cell-dot';

        const isWarpTile = (cell.has_warp || cell.underlying_symbol === 'D' || rawSym === 'D');
        const isCatwalk = ['╫', '╪', 'o'].includes(rawSym);
        const isStair = ['▲', '▼'].includes(rawSym);
        const isStoryTrigger = Boolean(rawSym === '!' || cell.is_story_gate_trigger === true || String(cell.kind || '').includes('剧情拦截') || String(cell.kind || '').includes('触发线'));
        const isNPC = Boolean(rawSym === 'N' || rawSym === 'T' || cell.is_npc_blocked === true);
        const isItem = (rawSym === 'h' || rawSym === 'I');

        // 优先级严格保障：主角 > 剧情拦截[!] > 传送大门[D] > 楼梯 > 独木桥[╪/╫] > NPC > 道具 > 草丛/水体 > 物理墙体 > 普通平地
        if (cell.is_player || rawSym === 'P') {
          sym = 'P';
          cellClass = isWarpTile ? 'cell-P cell-warp-player' : 'cell-P';
        } else if (isStoryTrigger) {
          sym = '!';
          cellClass = 'cell-story-trigger cell-blocked';
        } else if (rawSym === 'D' || cell.is_portal_doorway === true) {
          sym = 'D';
          cellClass = 'cell-warp';
        } else if (isStair) {
          // 立体双向符号法：在地面(Y=0)显示上行▲，在高台(Y=2)显示下行▼，直观呈现上下接驳
          sym = (fy >= 2) ? '▼' : '▲';
          cellClass = (fy >= 2) ? 'cell-stair cell-stair-down' : 'cell-stair cell-stair-up';
        } else if (isCatwalk) {
          sym = rawSym; // 严格保留 ╪ (入口) 与 ╫ (主体) 区分，契合 AGENTS.md 准则 7
          cellClass = 'cell-catwalk';
        } else if (rawSym === '↕') {
          sym = '↕';
          cellClass = 'cell-elevation-gap';
        } else if (isNPC) {
          sym = rawSym;
          cellClass = 'cell-npc';
        } else if (rawSym === 'h') {
          sym = 'h';
          cellClass = 'cell-grass';
        } else if (rawSym === 'I') {
          sym = 'I';
          cellClass = 'cell-grass';
        } else if (rawSym === '*') {
          sym = '*';
          cellClass = 'cell-grass';
        } else if (rawSym === 'W') {
          sym = 'W';
          cellClass = 'cell-water';
        } else if (rawSym === '~') {
          sym = '~';
          cellClass = 'cell-shore';
        } else if (!cell.walkable || cell.blocked || rawSym === '#' || rawSym === 'B') {
          sym = '#';
          cellClass = 'cell-wall cell-blocked';
        } else {
          sym = '.';
          cellClass = 'cell-dot';
        }

        const cellKey = `${cell.x},${cell.z},${cell.y}`;
        activeCellDataMap.set(cellKey, cell);

        const isSelected = (currentSelectedCell && currentSelectedCell.x === cell.x && currentSelectedCell.z === cell.z && currentSelectedCell.y === cell.y);
        const kindSafe = String(cell.kind || '普通地面').replace(/"/g, '&quot;');

        // 立体双模路径高亮裁决：
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
        }

        sliceHtml += `<div class="ds-map-cell ${cellClass} ${pathClass} ${isSelected ? 'selected' : ''}" 
          data-x="${cell.x}" data-z="${cell.z}" data-y="${cell.y}" 
          data-walkable="${cell.walkable}" data-kind="${kindSafe}" 
          data-symbol="${sym}"
          data-cellkey="${cellKey}"
          onmouseenter="handleCellHover(this)" 
          onclick="handleCellClick(this)">${cellDisplay}</div>`;
      });
      sliceHtml += `</div>`;
    });

    sliceHtml += `</div></div>`;
    return sliceHtml;
  }).join('');
}


// 楼层切片平滑聚焦滚动
function focusFloorSlice(fy) {
  const el = document.getElementById("slice-floor-" + fy);
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }
}

// 鼠标悬停动态轻量感知 (免冗余干扰)
function handleCellHover(el) {
  // 若用户尚未主动点击选点，悬停时自动预览瓦片物理属性
  if (!currentSelectedCell) {
    const x = parseInt(el.dataset.x, 10);
    const z = parseInt(el.dataset.z, 10);
    const y = parseInt(el.dataset.y, 10);
    const walkable = (el.dataset.walkable === 'true');
    const sym = el.dataset.symbol || '.';
    const kind = el.dataset.kind || '普通地面';
    const cellKey = `${x},${z},${y}`;
    renderTileInspection(cellKey, x, y, z, sym, walkable, kind);
  }
}

// 鼠标点击任意格子：拾取坐标、注入寻路台、实时更新瓦片档案卡并计算 A* 路径
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
  if (btnNav) {
    const cellObj = activeCellDataMap.get(cellKey) || {};
    const hasWarp = Boolean(cellObj.has_warp || sym === 'D' || cellObj.underlying_symbol === 'D');
    const isBuildingPortal = (cellObj.door_geometry?.type === 'building_portal');
    if (hasWarp && isBuildingPortal) {
      btnNav.innerText = '[一键顶门穿入 (/tasks)]';
    } else if (hasWarp) {
      btnNav.innerText = '[直达踩踏切图 (/tasks)]';
    } else {
      btnNav.innerText = '[立即直达选定格 (/tasks)]';
    }
    btnNav.style.display = walkable ? 'inline-flex' : 'none';
  }

  appendLog(`[地图选点] 拾取坐标 (X=${x}, Z=${z}, Y=${y})，材质: ${kind}，触发 A* 寻路规划...`);
  handlePlanNav();
}

// 快速导航到选定格子 (真实执行任务 POST /api/v1/navigation/tasks)
async function handleQuickNav() {
  if (!currentSelectedCell) return;
  appendLog(`[立即直达] 正在向网格坐标 (${currentSelectedCell.x}, ${currentSelectedCell.z}, Y=${currentSelectedCell.y}) 下发真实移动任务...`);
  handleExecuteNav();
}

// 轮询对战状态机 (严格区分非对战 / 野生战 / 训练家战)
async function pollBattleState() {
  if (simulatedBattleMode) return;
  try {
    const res = await fetch('/api/v1/battle/decisions');
    if (!res.ok) return;
    const data = await res.json();
    renderBattleState(data);
  } catch (e) {}
}

let cachedCurrentDecision = null;

async function pollCaptureEval() {
  try {
    const res = await fetch("/api/v1/battle/capture-eval");
    if (!res.ok) return;
    const d = await res.json();
    const rateElem = document.getElementById("wildCaptureRateVal");
    const textElem = document.getElementById("wildCaptureText");
    if (rateElem && d.estimated_catch_rate_percent !== undefined) {
      rateElem.innerText = d.estimated_catch_rate_percent.toFixed(1) + "%";
    }
    if (textElem && d.reason) {
      textElem.innerHTML = "预估捕获率: <strong style=\"color:var(--accent-green); font-size:15px;\" id=\"wildCaptureRateVal\">" + (d.estimated_catch_rate_percent || 0).toFixed(1) + "%</strong> (" + d.reason + ")";
    }
  } catch (e) {}
}

async function handleExecuteDecisionAction() {
  if (!cachedCurrentDecision) {
    appendLog("[AI决策执行] 当前暂无推荐决策");
    return;
  }
  const rec = cachedCurrentDecision;
  appendLog("[AI决策执行] 正在自动执行推荐操作: " + rec.type + " ...");
  if (rec.type === "use_move") {
    await handleMoveAction(rec.move_slot || 1);
  } else if (rec.type === "switch") {
    await handleSwitchPokemon(rec.party_slot || 2);
  } else if (rec.type === "run") {
    await handleRunAction();
  } else {
    appendLog("[AI决策执行] 未知动作类型: " + rec.type);
  }
}

function renderBattleState(data) {
  const inBattle = data.active === true;
  const notInBattleBox = document.getElementById("combatNotInBattle");
  const inBattleBox = document.getElementById("combatInBattle");
  const badge = document.getElementById("combatStatusBadge");

  if (!inBattle) {
    if (notInBattleBox) notInBattleBox.style.display = "flex";
    if (inBattleBox) inBattleBox.style.display = "none";
    if (badge) {
      badge.className = "ds-badge ds-badge-green";
      badge.innerText = "大地图常态 (无对战)";
    }
    return;
  }

  // 处于实战状态
  if (notInBattleBox) notInBattleBox.style.display = "none";
  if (inBattleBox) inBattleBox.style.display = "flex";
  if (badge) {
    badge.className = "ds-badge ds-badge-red";
    badge.innerText = "实战对抗中 (BUSY_BATTLE)";
  }

  const kind = data.battle_kind || "wild";
  const trainerBar = document.getElementById("trainerHeaderBar");
  const wildCard = document.getElementById("wildCaptureCard");
  const btnRun = document.getElementById("btnActionRun");
  const oppLabel = document.getElementById("oppKindLabel");

  // 1. 顶部 HUD 状态更新
  const kindBadge = document.getElementById("battleKindBadge");
  if (kindBadge) {
    kindBadge.className = kind === "trainer" ? "ds-badge ds-badge-amber" : "ds-badge ds-badge-red";
    kindBadge.innerText = kind === "trainer" ? "训练家对战 (Trainer Battle)" : "野生遭遇战 (Wild Battle)";
  }

  const phaseBadge = document.getElementById("battlePhaseBadge");
  if (phaseBadge) {
    const phaseStr = data.phase || "command_menu";
    phaseBadge.innerText = "Phase: " + phaseStr + (phaseStr === "command_menu" ? " (根命令)" : phaseStr === "move_menu" ? " (招式菜单)" : "");
    phaseBadge.className = phaseStr === "command_menu" ? "ds-badge ds-badge-cyan" : "ds-badge ds-badge-amber";
  }

  const cursorBadge = document.getElementById("battleCursorBadge");
  if (cursorBadge) {
    const cur = data.cursor || {};
    cursorBadge.innerText = "光标: " + (cur.slot ? ("槽位 " + cur.slot + " · " + (cur.grid || "")) : (cur.raw_u32 !== undefined ? ("0x" + cur.raw_u32.toString(16)) : "就绪"));
  }

  const canActBadge = document.getElementById("battleCanActBadge");
  if (canActBadge) {
    canActBadge.className = data.can_act ? "ds-badge ds-badge-green" : "ds-badge ds-badge-yellow";
    canActBadge.innerText = data.can_act ? "可行动: 就绪" : "等待推进 / 结算中";
  }

  if (kind === "trainer") {
    if (trainerBar) trainerBar.style.display = "flex";
    if (wildCard) wildCard.style.display = "none";
    if (oppLabel) oppLabel.innerText = "训练家出战宝可梦";
    if (btnRun) {
      btnRun.disabled = true;
      btnRun.innerText = "[面对训练家无法逃跑]";
      btnRun.className = "ds-btn ds-btn-ghost ds-btn-sm";
    }
  } else {
    if (trainerBar) trainerBar.style.display = "none";
    if (wildCard) wildCard.style.display = "flex";
    if (oppLabel) oppLabel.innerText = "野生遭遇宝可梦";
    if (btnRun) {
      btnRun.disabled = false;
      btnRun.innerText = "[脱离战斗 (原子逃跑 POST /battle/flee)]";
      btnRun.className = "ds-btn ds-btn-danger ds-btn-sm";
    }
    pollCaptureEval();
  }

  // 2. 渲染敌方宝可梦数据
  const opp = data.opponent?.active || data.opponent || {};
  const oppNameElem = document.getElementById("oppMonName");
  if (oppNameElem) {
    const oppName = opp.species?.names?.["zh-Hans"] || opp.species?.name || opp.species_name || "野生宝可梦";
    const oppEn = opp.species?.name || opp.name_en || "";
    const oppId = opp.species_id || opp.species?.id || "";
    oppNameElem.innerHTML = oppName + (oppEn ? " (" + oppEn + ")" : "") + (oppId ? " <span style=\"font-size:12px;color:var(--text-3);\">#" + oppId + "</span>" : "") + " <span style=\"font-size:13px;color:var(--text-3);margin-left:6px;\">Lv." + (opp.level || "?") + "</span>";
  }

  const oppHp = opp.current_hp ?? 0;
  const oppMaxHp = opp.max_hp || 1;
  const oppHpPct = Math.max(0, Math.min(100, Math.round((oppHp / oppMaxHp) * 100)));
  const oppHpBar = document.getElementById("oppMonHpBar");
  if (oppHpBar) {
    oppHpBar.style.width = oppHpPct + "%";
    oppHpBar.className = "ds-progress-fill " + (oppHpPct <= 20 ? "fill-red" : oppHpPct <= 50 ? "fill-amber" : "");
  }
  const oppHpText = document.getElementById("oppMonHpText");
  if (oppHpText) oppHpText.innerText = "生命值: " + oppHp + " / " + oppMaxHp + " (" + oppHpPct + "%)";

  const oppMeta = document.getElementById("oppMonMetaText");
  if (oppMeta) {
    const abilityName = opp.ability?.name || opp.ability?.name_en || opp.ability || "未揭示";
    const genderStr = opp.gender === "male" ? "♂ 雄性" : opp.gender === "female" ? "♀ 雌性" : "无性别";
    oppMeta.innerText = "特性: " + abilityName + " · 性别: " + genderStr;
  }

  // 3. 渲染我方出战宝可梦数据
  const pl = data.player?.active || data.player || {};
  const plNameElem = document.getElementById("playerMonName");
  if (plNameElem) {
    const plName = pl.species?.names?.["zh-Hans"] || pl.species?.name || pl.species_name || "我方首发";
    const plEn = pl.species?.name || pl.name_en || "";
    const plId = pl.species_id || pl.species?.id || "";
    plNameElem.innerHTML = plName + (plEn ? " (" + plEn + ")" : "") + (plId ? " <span style=\"font-size:12px;color:var(--text-3);\">#" + plId + "</span>" : "") + " <span style=\"font-size:13px;color:var(--text-3);margin-left:6px;\">Lv." + (pl.level || "?") + "</span>";
  }

  const plHp = pl.current_hp ?? 0;
  const plMaxHp = pl.max_hp || 1;
  const plHpPct = Math.max(0, Math.min(100, Math.round((plHp / plMaxHp) * 100)));
  const plHpBar = document.getElementById("playerMonHpBar");
  if (plHpBar) {
    plHpBar.style.width = plHpPct + "%";
    plHpBar.className = "ds-progress-fill " + (plHpPct <= 20 ? "fill-red" : plHpPct <= 50 ? "fill-amber" : "");
  }
  const plHpText = document.getElementById("playerMonHpText");
  if (plHpText) plHpText.innerText = "生命值: " + plHp + " / " + plMaxHp + " (" + plHpPct + "%)";

  const plMeta = document.getElementById("playerMonMetaText");
  if (plMeta) {
    const plAbility = pl.ability?.name || pl.ability?.name_en || pl.ability || "特性";
    const plGender = pl.gender === "male" ? "♂ 雄性" : pl.gender === "female" ? "♀ 雌性" : "无性别";
    plMeta.innerText = "特性: " + plAbility + " · 性别: " + plGender;
  }

  const plStats = document.getElementById("playerMonStatsText");
  if (plStats && pl.stats) {
    plStats.innerText = "攻 " + (pl.stats.attack || "-") + " / 防 " + (pl.stats.defense || "-") + " / 特攻 " + (pl.stats.special_attack || "-") + " / 特防 " + (pl.stats.special_defense || "-") + " / 速度 " + (pl.stats.speed || "-");
  }

  // 4. 渲染 AI 推荐战术 Banner
  const dec = data.decision || {};
  const rec = dec.recommended_action || data.recommended_action || {};
  const decText = document.getElementById("battleDecisionText");
  if (decText) {
    if (rec.type === "use_move") {
      decText.innerHTML = "<strong style=\"color:var(--accent-green);\">推荐出招:</strong> 使用「" + (rec.move_name || ("招式" + rec.move_slot)) + "」 · " + (rec.reason || "");
    } else if (rec.type === "switch") {
      decText.innerHTML = "<strong style=\"color:var(--accent-amber);\">推荐换人:</strong> 切换席位 #" + rec.party_slot + " · " + (rec.reason || "");
    } else if (rec.type === "run") {
      decText.innerHTML = "<strong style=\"color:var(--accent-cyan);\">推荐脱战:</strong> " + (rec.reason || "脱离战斗");
    } else {
      decText.innerText = rec.reason || "正在实时评估最优招式与相克关系...";
    }
  }
  cachedCurrentDecision = rec;

  // 5. 渲染 4 招式卡片
  const moves = pl.moves || [];
  const evalMoves = dec.moves_evaluated || [];
  const bestMove = dec.best_move || {};
  renderBattleMoves(moves, evalMoves, bestMove);

  // 6. 渲染全队在战换人面板
  renderBattlePartySwitchDeck(data.player?.party || cachedParty || [], pl.species_id);

  // 7. 渲染底层 ARM9 RAM 结构体回读
  renderBattleRamDump(data);
}

function renderBattleMoves(moves, evalMoves, bestMove) {
  const container = document.getElementById("battleMovesGrid");
  if (!container) return;
  if (!moves || moves.length === 0) {
    container.innerHTML = "<div style=\"padding:12px; color:var(--text-3); grid-column:span 2; text-align:center;\">等待读取出战宝可梦招式...</div>";
    return;
  }

  const evalMap = {};
  if (Array.isArray(evalMoves)) {
    evalMoves.forEach(em => { evalMap[em.slot] = em; });
  }

  container.innerHTML = moves.map(m => {
    const slot = m.slot;
    const em = evalMap[slot] || {};
    const moveName = m.name || m.name_zh || em.name_zh || ("招式 " + slot);
    const moveEn = m.name_en || em.name_en || "";
    const moveType = m.type || em.move_type_name || "一般";
    const curPp = m.current_pp ?? em.current_pp ?? 0;
    const maxPp = m.max_pp ?? em.max_pp ?? 0;
    const isOut = (curPp === 0);
    const isBest = (bestMove && bestMove.slot === slot) || (em.verdict === "best");
    const mult = em.type_multiplier !== undefined ? em.type_multiplier : 1.0;
    const power = m.power ?? em.base_power ?? "-";
    const acc = m.accuracy ?? em.accuracy ?? 100;
    const score = em.expected_score !== undefined ? em.expected_score.toFixed(1) : "-";

    let multBadge = "";
    if (mult > 1.0) multBadge = "<span class=\"ds-badge ds-badge-green\" style=\"font-size:11px;\">克制 " + mult + "x</span>";
    else if (mult === 0.0) multBadge = "<span class=\"ds-badge ds-badge-red\" style=\"font-size:11px;\">无效 0x</span>";
    else if (mult < 1.0) multBadge = "<span class=\"ds-badge ds-badge-amber\" style=\"font-size:11px;\">微弱 " + mult + "x</span>";

    return (
      "<div class=\"ds-move-card " + (isBest ? "recommended" : "") + " " + (isOut ? "disabled" : "") + "\" onclick=\"" + (isOut ? "" : "handleMoveAction(" + slot + ")") + "\">" +
        "<div style=\"display:flex; justify-content:space-between; align-items:center;\">" +
          "<div style=\"display:flex; align-items:center; gap:6px;\">" +
            "<span class=\"ds-badge ds-badge-cyan\" style=\"font-weight:800; font-size:11px;\">#" + slot + "</span>" +
            "<strong style=\"font-size:14px; color:" + (isBest ? "var(--accent-green)" : "var(--text-1)") + ";\">" + moveName + "</strong>" +
            (moveEn ? "<span style=\"font-size:11px; color:var(--text-3); font-weight:normal;\">" + moveEn + "</span>" : "") +
          "</div>" +
          "<div style=\"display:flex; gap:4px; align-items:center;\">" +
            "<span class=\"ds-badge\" style=\"font-size:11px; font-weight:700;\">[" + moveType + "]</span>" +
            multBadge +
          "</div>" +
        "</div>" +
        "<div style=\"display:flex; justify-content:space-between; font-size:12px; color:var(--text-2); margin-top:2px;\">" +
          "<span>威力: <strong>" + power + "</strong> · 命中: <strong>" + acc + "</strong></span>" +
          "<span>PP: <strong style=\"color:" + (curPp > 3 ? "var(--accent-green)" : curPp > 0 ? "var(--accent-amber)" : "var(--accent-red)") + "; font-size:13px;\">" + curPp + " / " + maxPp + "</strong></span>" +
        "</div>" +
        "<div style=\"display:flex; justify-content:space-between; align-items:center; font-size:11px; margin-top:2px;\">" +
          "<span style=\"color:var(--text-3);\">AI 评分: <strong style=\"color:var(--accent-cyan);\">" + score + "</strong></span>" +
          (isBest ? "<span class=\"ds-badge ds-badge-green\" style=\"font-size:11px; font-weight:800;\">★ AI 最优推荐出招</span>" : (isOut ? "<span style=\"color:var(--accent-red); font-weight:700;\">[PP已耗尽]</span>" : "<span style=\"color:var(--text-3);\">[点击直接出招]</span>")) +
        "</div>" +
      "</div>"
    );
  }).join("");
}

function renderBattlePartySwitchDeck(party, activeSpeciesId) {
  const container = document.getElementById("battlePartyRoster");
  if (!container) return;
  if (!party || party.length === 0) {
    container.innerHTML = "<div style=\"padding:8px; color:var(--text-3); grid-column:span 3; text-align:center;\">暂无全队数据</div>";
    return;
  }

  container.innerHTML = party.map(s => {
    const slot = s.slot;
    const name = s.species_name_zh || s.species_name || s.species || ("席位 " + slot);
    const hp = s.current_hp ?? 0;
    const maxHp = s.max_hp || 1;
    const hpPct = Math.max(0, Math.min(100, Math.round((hp / maxHp) * 100)));
    const isActive = (s.species === activeSpeciesId || s.species_id === activeSpeciesId || slot === 1 && !activeSpeciesId);
    const isFainted = (hp === 0);

    return (
      "<div class=\"ds-party-slot " + (isActive ? "is-lead" : "") + " " + (isFainted ? "is-crit" : "") + "\" style=\"padding:8px 10px; gap:4px; font-size:12px;\">" +
        "<div style=\"display:flex; justify-content:space-between; align-items:center;\">" +
          "<strong style=\"color:" + (isActive ? "var(--accent-cyan)" : "var(--text-1)") + ";\">" + slot + ". " + name + "</strong>" +
          "<span class=\"ds-badge " + (isActive ? "ds-badge-cyan" : isFainted ? "ds-badge-red" : "") + "\" style=\"font-size:10px;\">" +
            (isActive ? "★ 战斗中" : isFainted ? "濒死" : "Lv." + (s.level || "?")) +
          "</span>" +
        "</div>" +
        "<div class=\"ds-progress\" style=\"height:4px; margin-top:2px;\">" +
          "<div class=\"ds-progress-fill " + (hpPct <= 20 ? "fill-red" : hpPct <= 50 ? "fill-amber" : "") + "\" style=\"width:" + hpPct + "%;\"></div>" +
        "</div>" +
        "<div style=\"display:flex; justify-content:space-between; align-items:center; margin-top:2px; font-size:11px;\">" +
          "<span style=\"color:var(--text-3);\">" + hp + " / " + maxHp + "</span>" +
          (isActive ? "<span style=\"color:var(--accent-cyan); font-weight:700;\">出战中</span>" : isFainted ? "<span style=\"color:var(--accent-red);\">无法换入</span>" : ("<button class=\"ds-btn ds-btn-sm ds-btn-yellow\" style=\"padding:2px 8px; font-size:11px;\" onclick=\"handleSwitchPokemon(" + slot + ")\">[换人出战]</button>")) +
        "</div>" +
      "</div>"
    );
  }).join("");
}

function renderBattleRamDump(data) {
  const oppDump = document.getElementById("ramOppDump");
  const plDump = document.getElementById("ramPlayerDump");
  const opp = data.opponent?.active || data.opponent || {};
  const pl = data.player?.active || data.player || {};

  if (oppDump) {
    oppDump.innerHTML = (
      "<div style=\"color:var(--accent-red); font-weight:800; border-bottom:1px solid #333; padding-bottom:4px; margin-bottom:6px;\">[敌方 BattlePokeParam · 0x0225BCA8]</div>" +
      "<div>物种 ID: 0x" + ((opp.species_id || 0).toString(16).toUpperCase().padStart(4, "0")) + " (" + (opp.species_name_zh || opp.species || "未知") + ")</div>" +
      "<div>等级: " + (opp.level || "?") + " · 性别: " + (opp.gender || "无") + " · PID: 0x" + (opp.pid || "--------") + "</div>" +
      "<div>当前 HP: " + (opp.current_hp || 0) + " / 最大 HP: " + (opp.max_hp || 0) + "</div>" +
      "<div>特性 ID: " + (opp.ability?.id || "未揭示") + " (" + (opp.ability?.name || "") + ")</div>" +
      "<div>招式列表: " + ((opp.moves || []).map(m => m.name + "(" + m.current_pp + "/" + m.max_pp + ")").join(" · ") || "未探针") + "</div>"
    );
  }

  if (plDump) {
    plDump.innerHTML = (
      "<div style=\"color:var(--accent-green); font-weight:800; border-bottom:1px solid #333; padding-bottom:4px; margin-bottom:6px;\">[我方 BattlePokeParam · 0x0225B418]</div>" +
      "<div>物种 ID: 0x" + ((pl.species_id || 0).toString(16).toUpperCase().padStart(4, "0")) + " (" + (pl.species_name_zh || pl.species || "我方首发") + ")</div>" +
      "<div>等级: " + (pl.level || "?") + " · 性别: " + (pl.gender || "无") + " · PID: 0x" + (pl.pid || "--------") + "</div>" +
      "<div>当前 HP: " + (pl.current_hp || 0) + " / 最大 HP: " + (pl.max_hp || 0) + "</div>" +
      "<div>特性 ID: " + (pl.ability?.id || "39") + " (" + (pl.ability?.name || "精神力") + ")</div>" +
      "<div>五维能力: 攻" + (pl.stats?.attack || "-") + " 防" + (pl.stats?.defense || "-") + " 特攻" + (pl.stats?.special_attack || "-") + " 特防" + (pl.stats?.special_defense || "-") + " 速" + (pl.stats?.speed || "-") + "</div>" +
      "<div>招式列表: " + ((pl.moves || []).map(m => m.name + "(" + m.current_pp + "/" + m.max_pp + ")").join(" · ")) + "</div>"
    );
  }
}

function toggleSimulatedBattle() {
  simulatedBattleMode = !simulatedBattleMode;
  if (simulatedBattleMode) {
    renderBattleState({
      active: true,
      battle_kind: 'trainer',
      opponent: {
        species_name_zh: '双斧战龙',
        level: 48,
        current_hp: 145,
        max_hp: 145
      }
    });
    appendLog('[模拟模式] 已切换为训练家战斗视图测试 (夏卡 · 双斧战龙 Lv.48)。');
  } else {
    renderBattleState({ active: false });
    appendLog('[模拟模式] 已切回大地图常态无对战视图。');
  }
}

// 按需 3D WebGL 实景控制器 (惰性挂载，离开休眠，0% 额外 GPU)
function toggle3DView() {
  const iframe = document.getElementById('iframe3D');
  const placeholder = document.getElementById('placeholder3D');
  const badge = document.getElementById('badge3DState');
  const btn = document.getElementById('btnToggle3D');

  if (!is3DActive) {
    iframe.src = '/frontend/workbench.html#world';
    iframe.style.display = 'block';
    placeholder.style.display = 'none';
    is3DActive = true;
    badge.className = 'ds-badge ds-badge-cyan';
    badge.innerText = '状态: 3D 实景渲染中 (已连接 ROM 3D 网格)';
    btn.className = 'ds-btn ds-btn-sm ds-btn-danger';
    btn.innerText = '[挂起休眠 3D (释放 GPU)]';
    appendLog('[3D 实景] 已成功挂载 WebGL 渲染视窗，开始渲染 ROM 3D 建筑与角色...');
  } else {
    pause3DView();
  }
}

function pause3DView() {
  const iframe = document.getElementById('iframe3D');
  const placeholder = document.getElementById('placeholder3D');
  const badge = document.getElementById('badge3DState');
  const btn = document.getElementById('btnToggle3D');

  if (iframe) {
    iframe.src = 'about:blank';
    iframe.style.display = 'none';
  }
  if (placeholder) placeholder.style.display = 'flex';
  if (badge) {
    badge.className = 'ds-badge';
    badge.innerText = '状态: 渲染器休眠中 (0% GPU/CPU)';
  }
  if (btn) {
    btn.className = 'ds-btn ds-btn-sm ds-btn-primary';
    btn.innerText = '[启动 3D WebGL 实景渲染]';
  }
  is3DActive = false;
  appendLog('[3D 实景] 3D 场景已挂起休眠，WebGL 上下文已释放，显卡资源已归零。');
}


// 轮询主线剧情道闸与徽章 (GET /api/v1/progression/state)
async function pollStoryProgression() {
  try {
    const res = await fetch('/api/v1/progression/state');
    if (!res.ok) return;
    const data = await res.json();

    // 1. 金钱与名人堂
    const moneyElem = document.getElementById('moneyAmountText');
    if (moneyElem && data.money?.formatted) {
      moneyElem.innerText = data.money.formatted;
    }
    const hofElem = document.getElementById('hallOfFameText');
    if (hofElem && data.player_data?.hall_of_fame_count !== undefined) {
      hofElem.innerText = `${data.player_data.hall_of_fame_count} 次`;
    }

    // 2. 徽章列表动态渲染
    const badgesContainer = document.getElementById('badgesList');
    if (badgesContainer && data.badges?.individual) {
      badgesContainer.innerHTML = data.badges.individual.map(b => `
        <span class="ds-badge ${b.obtained ? 'ds-badge-amber' : ''}">
          [${b.obtained ? '已获得' : '待挑战'}] ${b.name_zh}
        </span>
      `).join('');
    }

    // 3. 里程碑进度渲染
    const timeline = document.getElementById('milestonesTimeline');
    if (timeline && data.badges?.individual) {
      const count = data.badges.count || 0;
      const milestoneNames = [
        'M1 算木牧场', 'M2 基础徽章', 'M3 毒性徽章', 'M4 甲虫徽章', 'M5 伏特徽章',
        'M6 震动徽章', 'M7 飞翼徽章', 'M8 冰冻徽章', 'M9 海浪徽章', 'M10 联盟冠军'
      ];
      timeline.innerHTML = milestoneNames.map((name, idx) => {
        let cls = '';
        let prefix = '[待挑战]';
        if (idx < count + 1) {
          cls = 'completed';
          prefix = '[完成]';
        } else if (idx === count + 1) {
          cls = 'active';
          prefix = '[进行中]';
        }
        return `<div class="ds-step ${cls}">${prefix} ${name}</div>`;
      }).join('');
    }

    // 4. 12 处剧情道闸实时监测
    const gatesContainer = document.getElementById('storyGatesContainer');
    if (gatesContainer && data.story_gates) {
      gatesContainer.innerHTML = data.story_gates.map(g => {
        const isOk = g.unlocked;
        return `
          <div style="color:${isOk ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight:600; margin-bottom:4px;">
            [${isOk ? '放行' : '阻挡'}] ${g.name_zh}: ${g.reason_zh || (isOk ? '通行无阻' : '未满足条件')}
          </div>
        `;
      }).join('');
    }

    // 5. 顶栏主线目标
    const pillTarget = document.getElementById('pillTarget');
    if (pillTarget && data.badges?.next_target) {
      pillTarget.innerText = `主线目标: 第${data.badges.next_target.badge_id}道馆 (${data.badges.next_target.name_zh})`;
    }
  } catch (e) {}
}

// 轮询队伍 6 只宝可梦全员健康状态 (渲染在对战页面的 3x2 网格中)
async function pollParty() {
  try {
    const res = await fetch('/api/v1/game/party');
    if (!res.ok) return;
    const data = await res.json();
    const slots = data.slots || [];
    const container = document.getElementById('partyGridList');
    if (!container || slots.length === 0) return;

    let html = '';
    slots.forEach(mon => {
      const hp = mon.current_hp ?? 0;
      const maxHp = mon.max_hp ?? 1;
      const pct = Math.max(0, Math.min(100, Math.round((hp / maxHp) * 100)));
      const isCrit = hp <= Math.ceil(maxHp * 0.2);
      const isLead = (mon.slot === 1);
      const slotClass = isLead ? 'is-lead' : isCrit ? 'is-crit' : '';
      const fillClass = isCrit ? 'fill-red' : (pct <= 50 ? 'fill-amber' : '');
      const moves = (mon.moves || []).map(m => `${m.name_zh || m.name || '招式'}(${m.current_pp ?? '?'}/${m.max_pp ?? '?'})`).join(' ');

      html += `
        <div class="ds-party-slot ${slotClass}">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <strong style="color:${isCrit ? 'var(--accent-red)' : (isLead ? 'var(--accent-cyan)' : 'var(--text-1)')}; font-size:14px;">
              ${mon.slot}. ${mon.species_name_zh || mon.name || '宝可梦'}
            </strong>
            <span class="ds-badge ${isLead ? 'ds-badge-cyan' : isCrit ? 'ds-badge-red' : ''}">
              ${isLead ? '首发出战 · ' : ''}等级 ${mon.level || 1} ${isCrit ? '[濒危需治疗]' : ''}
            </span>
          </div>
          <div class="ds-progress" style="margin-top:2px;">
            <div class="ds-progress-fill ${fillClass}" style="width:${pct}%;"></div>
          </div>
          <div style="font-size:12px; color:var(--text-2); display:flex; justify-content:space-between;">
            <span>生命值: ${hp} / ${maxHp}</span>
            <span>${pct}% ${isCrit ? '· 濒危' : '· 正常'}</span>
          </div>
          <div style="font-size:11px; color:var(--text-3); line-height:1.5;">
            招式: ${moves || '未解析技能数据'}
          </div>
        </div>
      `;
    });
    container.innerHTML = html;
    if (slots[0]?.moves) { renderBattleMoves(slots[0].moves); }
  } catch (e) {}
}

// 轮询背包 110 项真实库存
let cachedInventory = [];
async function pollInventory() {
  try {
    const res = await fetch('/api/v1/game/inventory');
    if (!res.ok) return;
    const data = await res.json();
    const pockets = data.pockets || [];
    cachedInventory = [];
    pockets.forEach(pkt => {
      (pkt.items || []).forEach(item => {
        cachedInventory.push({
          id: item.item_id,
          name_zh: item.name || item.name_zh || '-',
          name_en: item.name_en || item.identifier || '-',
          pocket: pkt.name_zh || pkt.pocket_id || '常规道具',
          qty: item.quantity || 1
        });
      });
    });
    renderInventoryTable(cachedInventory);
  } catch (e) {}
}

function renderInventoryTable(items) {
  const tbody = document.querySelector('#inventoryTable tbody');
  if (!tbody) return;
  if (items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-3); height:60px;">未找到匹配的背包道具</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(i => `
    <tr>
      <td>${i.id}</td>
      <td><strong>${i.name_zh}</strong></td>
      <td>${i.name_en}</td>
      <td><span class="ds-badge">${i.pocket}</span></td>
      <td>${i.qty}</td>
    </tr>
  `).join('');
}

function filterInventory(query) {
  const q = query.toLowerCase().trim();
  if (!q) {
    renderInventoryTable(cachedInventory);
    return;
  }
  const filtered = cachedInventory.filter(i => 
    String(i.id).includes(q) ||
    i.name_zh.toLowerCase().includes(q) ||
    i.name_en.toLowerCase().includes(q) ||
    i.pocket.toLowerCase().includes(q)
  );
  renderInventoryTable(filtered);
}

// 主线推进指令下发
const PRESET_MOVE_MAP = {
  525: '龙尾 (#525) · 龙系 · 威力 60 · PP 10 (TM82)',
  474: '毒液冲击 (#474) · 毒系 · 威力 65 · PP 10 (TM09)',
  523: '重踏 (#523) · 地面系 · 威力 60 · PP 20 (TM78)',
  521: '伏特替换 (#521) · 电系 · 威力 70 · PP 20 (TM72)',
  89: '地震 (#89) · 地面系 · 威力 100 · PP 10 (TM26)',
  85: '十万伏特 (#85) · 电系 · 威力 90 · PP 15 (TM24)',
  53: '喷射火焰 (#53) · 火系 · 威力 90 · PP 15 (TM35)',
  533: '圣剑 (#533) · 格斗系 · 威力 90 · PP 20 (无视防御)',
  442: '铁头 (#442) · 钢系 · 威力 80 · PP 15 (本系强攻)',
  14: '剑舞 (#14) · 一般 · 变化 · 物攻+2阶',
  349: '龙之舞 (#349) · 龙系 · 变化 · 物攻+1/速度+1',
  19: '飞翔 (#19) · 飞行系 · 威力 90 · PP 15 (大地图飞行)',
  57: '冲浪 (#57) · 水系 · 威力 90 · PP 15 (水路航行)',
  82: '龙之怒 (#82) · 龙系 · 固定40点伤害 · PP 10',
  141: '吸血 (#141) · 虫系 · 威力 20 · PP 15 (吸收50%伤害)',
  512: '杂技 (#512) · 飞行系 · 威力 55/110 · PP 15',
  406: '龙之波动 (#406) · 龙系 · 威力 85 · PP 10'
};

function handlePresetMoveSelect(val) {
  const input = document.getElementById('teachMoveId');
  if (input && val) {
    input.value = val;
    updateMovePreview(val);
  }
}

function updateMovePreview(val) {
  const badge = document.getElementById('movePreviewBadge');
  if (!badge) return;
  const num = parseInt(val, 10);
  if (!num || num <= 0) {
    badge.innerText = '请输入技能编号 (1..559)';
    badge.className = 'ds-badge';
    return;
  }
  const preview = PRESET_MOVE_MAP[num];
  if (preview) {
    badge.innerText = preview;
    badge.className = 'ds-badge ds-badge-cyan';
  } else {
    badge.innerText = '技能 #' + num + ' (自定义招式编号)';
    badge.className = 'ds-badge ds-badge-amber';
  }
}

async function handlePartyTeachMove() {
  const pSlot = parseInt(document.getElementById('teachPartySlot')?.value || '1', 10);
  const mSlot = parseInt(document.getElementById('teachMoveSlot')?.value || '1', 10);
  const mId = parseInt(document.getElementById('teachMoveId')?.value || '82', 10);
  appendLog('[????] ??????? ' + pSlot + ' ?? ' + mSlot + ' ???? #' + mId + ' (POST /api/v1/game/party/teach-move)...');
  try {
    const res = await fetch('/api/v1/game/party/teach-move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ party_slot: pSlot, move_slot: mSlot, move_id: mId })
    });
    const d = await res.json();
    if (res.ok) {
      const pName = d.pokemon ? ? Lv.? : '';
      const oldName = d.old_move ? ?? : '';
      const newName = d.new_move ? ?? : ??;
      appendLog([????] ??  ??  ??? ?????? ????RAM???: );
      if (d.current_moves && d.current_moves.length) {
        appendLog([??????] );
      }
      pollParty();
    } else {
      appendLog('[????] ' + (d.detail || JSON.stringify(d)));
    }
  } catch (e) {
    appendLog('[????] ' + e);
  }
}

async function handlePartySwapOrder() {
  const slotA = parseInt(document.getElementById('partySwapSlotA')?.value || 1, 10);
  const slotB = parseInt(document.getElementById('partySwapSlotB')?.value || 2, 10);
  appendLog("[????] ???????? " + slotA + " ? " + slotB + " (POST /api/v1/game/party/swap)...");
  try {
    const res = await fetch('/api/v1/game/party/swap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slot_a: slotA, slot_b: slotB })
    });
    const d = await res.json();
    if (res.ok) {
      if (d.swapped_a && d.swapped_b) {
        appendLog([????] ?? ? Lv.?? ?? ? Lv.???????);
      } else {
        appendLog([????] ??  ?  ????????????);
      }
      if (d.latest_lineup && d.latest_lineup.length) {
        appendLog([??????] );
      }
      if (d.lead_pokemon) {
        appendLog([??????] ?? 1? Lv.?(????));
      }
      pollParty();
    } else {
      appendLog("[????] " + (d.detail || JSON.stringify(d)));
    }
  } catch (e) {
    appendLog("[????] " + e);
  }
}

async function handleStepForward() {
  appendLog('[指令下发] 正在调用主线自驱动步进接口 POST /api/v1/agent/story/step...');
  try {
    const res = await fetch('/api/v1/agent/story/step', { method: 'POST' });
    const data = await res.json();
    appendLog(`[执行成功] 主线步进结果: ${JSON.stringify(data)}`);
    pollPlayerRuntime();
  } catch (e) {
    appendLog(`[执行失败] 主线步进发生异常: ${e}`);
  }
}

async function handleNurseRecovery() {
  appendLog('[????] ?? POST /api/v1/agent/automation/recovery ??????????...');
  try {
    const res = await fetch('/api/v1/agent/automation/recovery', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ service: 'nearest', movement_mode: 'auto' })
    });
    const d = await res.json();
    if (res.ok) {
      appendLog([??????] ?? ID: , ??: );
      appendNavTaskLog([??????] ??????????? (2100???)..., 'var(--accent-blue)');
      if (d.task_id) {
        for (let i = 0; i < 20; i++) {
          await new Promise(r => setTimeout(r, 800));
          const tRes = await fetch(/api/v1/agent/automation/tasks/);
          if (tRes.ok) {
            const td = await tRes.json();
            if (td.status === 'succeeded') {
              appendLog([??????] ????????????????????);
              appendNavTaskLog([??????] ?????????? (2100???), 'var(--accent-green)');
              break;
            } else if (td.status === 'failed') {
              appendLog([???????] );
              break;
            }
          }
        }
      }
      pollParty();
    } else {
      appendLog([????] );
    }
  } catch (e) {
    appendLog([????] );
    appendNavTaskLog([????] , 'var(--accent-red)');
  }
}

async function handleMoveAction(slot) {
  appendLog("[招式指令] 正在执行招式槽位 " + slot + " (POST /api/v1/battle/move)...");
  try {
    const res = await fetch("/api/v1/battle/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ move_slot: slot })
    });
    const d = await res.json();
    appendLog("[招式结果] 状态: " + d.status + ", 是否执行: " + d.executed + ", 校验: " + JSON.stringify(d.verification || d.reason || {}));
    pollBattleState();
  } catch (e) {
    appendLog("[招式异常] " + e);
  }
}

async function handleCatchAction(itemId) {
  itemId = itemId || 4;
  appendLog("[投球指令] 投掷精灵球 (POST /api/v1/battle/catch, item_id=" + itemId + ")...");
  try {
    const res = await fetch("/api/v1/battle/catch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: itemId })
    });
    const d = await res.json();
    appendLog("[投球结果] 状态: " + d.status + ", 是否执行: " + d.executed + ", 校验: " + JSON.stringify(d.verification || d.reason || {}));
    pollBattleState();
    pollInventory();
  } catch (e) {
    appendLog("[投球异常] " + e);
  }
}

async function handleRunAction() {
  appendLog("[脱战指令] 发送原子逃跑指令 (POST /api/v1/battle/flee)...");
  try {
    const res = await fetch("/api/v1/battle/flee", {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    const d = await res.json();
    appendLog("[脱战结果] 状态: " + d.status + ", 是否成功: " + d.ok + ", 消息: " + (d.message || ""));
    pollBattleState();
    pollPlayerRuntime();
  } catch (e) {
    appendLog("[脱战异常] " + e);
  }
}

async function handleBattleItemAction(itemId, partySlot) {
  itemId = itemId || 17;
  partySlot = partySlot || 1;
  appendLog("[战斗用药] 使用道具 #" + itemId + " 至队伍槽位 " + partySlot + " (POST /api/v1/battle/item)...");
  try {
    const res = await fetch("/api/v1/battle/item", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: itemId, target_party_slot: partySlot })
    });
    const d = await res.json();
    appendLog("[道具结果] 状态: " + d.status + ", 是否执行: " + d.executed + ", 校验: " + JSON.stringify(d.verification || d.reason || {}));
    pollBattleState();
    pollInventory();
    pollParty();
  } catch (e) {
    appendLog("[道具异常] " + e);
  }
}

async function handleBattleSurveyAction(count) {
  count = count || 3;
  appendLog("[对战逆向测绘] 启动多轮自动化巡逻与对战逆向测绘 (POST /api/v1/battle/survey, 轮数=" + count + ")...");
  try {
    const res = await fetch("/api/v1/battle/survey", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ battles_to_run: count })
    });
    const d = await res.json();
    appendLog("[测绘报告] 完成轮数: " + (d.rounds_completed || 0) + ", 结果: " + JSON.stringify(d.summary || d));
    pollBattleState();
    pollPlayerRuntime();
  } catch (e) {
    appendLog("[测绘异常] " + e);
  }
}

async function handleBicycleMount() {
  appendLog('[载具调度] 调用 POST /api/v1/player/bicycle/mount 尝试骑上自行车...');
  try {
    const res = await fetch('/api/v1/player/bicycle/mount', { method: 'POST' });
    const d = await res.json();
    if (res.ok) {
      appendLog(`[骑车成功] 状态: ${d.status}, 姿态: ${d.transport_mode}, 速度: 4 帧/格`);
    } else {
      appendLog(`[骑车提示] ${d.detail?.reason || d.detail?.status || JSON.stringify(d)}`);
    }
    pollPlayerRuntime();
  } catch (e) {
    appendLog(`[骑车异常] ${e}`);
  }
}

// 走下自行车 (POST /api/v1/player/bicycle/dismount)
async function handleBicycleDismount() {
  appendLog('[载具调度] 调用 POST /api/v1/player/bicycle/dismount 走下自行车...');
  try {
    const res = await fetch('/api/v1/player/bicycle/dismount', { method: 'POST' });
    const d = await res.json();
    appendLog(`[下车响应] 状态: ${d.status || d.detail?.status}, 姿态: ${d.transport_mode || 'OnFoot'}`);
    pollPlayerRuntime();
  } catch (e) {
    appendLog(`[下车异常] ${e}`);
  }
}

function setLocomotion(mode) {
  const modeNames = {
    'run': '连续奔跑 (按住B键, 8帧/格)',
    'bike': '自行车极速 (快捷键Y, 4帧/格)',
    'walk': '常规步行 (16帧/格)',
    'surf': '冲浪水路模式'
  };
  const selectElem = document.getElementById('navModeSelect');
  if (selectElem) selectElem.value = mode;
  appendLog(`[步态切换] 移动模式已变更为: ${modeNames[mode] || mode} (启用同向直线批量合并)`);
  appendNavTaskLog(`[步态切换] 移动策略变更为: ${modeNames[mode] || mode}`, 'var(--accent-cyan)');
  if (currentSelectedCell) {
    handlePlanNav();
  }
}


let activeNavTaskId = null;

// 真实下发移动任务执行 (POST /api/v1/navigation/tasks)
async function handleExecuteNav() {
  const tx = parseInt(document.getElementById('navTargetX')?.value || String(livePlayerGrid.x), 10);
  const tz = parseInt(document.getElementById('navTargetZ')?.value || String(livePlayerGrid.z >= 2 ? livePlayerGrid.z - 2 : livePlayerGrid.z + 2), 10);
  const rawY = document.getElementById('navTargetY')?.value;
  const ty = (rawY !== undefined && rawY !== '') ? parseInt(rawY, 10) : (livePlayerGrid.y ?? 0);
  const mode = document.getElementById('navModeSelect')?.value || 'auto';

  const targetCell = activeCellDataMap.get(`${tx},${tz},${ty}`);
  if (targetCell && targetCell.walkable === false) {
    appendNavTaskLog(`[下发拒绝] 选定目标 (X=${tx}, Z=${tz}, Y=${ty}) 为不可站立的障碍/悬空虚空 (${targetCell.symbol} · ${targetCell.kind || '障碍'})，无法作为目的地！请点击绿色可通行格。`, 'var(--accent-amber)');
    return;
  }
  appendNavTaskLog(`[下发任务] POST /api/v1/navigation/tasks 驱动角色移动至 (X=${tx}, Z=${tz}, Y=${ty}), 策略=${mode}...`, 'var(--accent-cyan)');
  appendLog(`[下发任务] 调用 POST /api/v1/navigation/tasks 驱动角色移动至 (${tx}, ${tz}, Y=${ty})...`);

  const statusBadge = document.getElementById('navLiveTaskStatusBadge');
  if (statusBadge) {
    statusBadge.className = 'ds-badge ds-badge-yellow';
    statusBadge.innerHTML = '<span class="ds-dot"></span>任务排队中...';
  }
  const btnResume = document.getElementById('btnResumeNav');
  const btnResumeMon = document.getElementById('btnResumeNavMonitor');
  if (btnResume) btnResume.style.display = 'none';
  if (btnResumeMon) btnResumeMon.style.display = 'none';

  const destPayload = {
    type: "grid",
    space: "gen5-field-grid-v1",
    zone_id: livePlayerZone || 457,
    x: tx,
    y: ty,
    z: tz
  };

  try {
    const res = await fetch('/api/v1/navigation/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        destination: destPayload,
        movement_mode: mode
      })
    });
    const d = await res.json();
    if (res.ok && (d.task_id || d.id)) {
      activeNavTaskId = d.task_id || d.id;
      const totalSteps = d.progress?.total_steps || d.total_steps || (activePlannedPathMap.size > 0 ? activePlannedPathMap.size : '?');
      appendNavTaskLog(`[任务启动] 任务 ID: ${activeNavTaskId}, 模式: ${d.movement_mode || d.movement?.selected || mode}, 总步数: ${totalSteps}`, 'var(--accent-green)');
      appendLog(`[任务启动] 任务 ID: ${activeNavTaskId}, 状态: ${d.status}`);
      const btnCancel = document.getElementById('btnCancelNav');
      if (btnCancel) btnCancel.style.display = 'inline-flex';
      trackNavTask(activeNavTaskId);
    } else {
      const errMsg = d.detail?.message || d.detail?.reason || d.error?.message || (typeof d.detail === 'string' ? d.detail : JSON.stringify(d));
      appendNavTaskLog(`[下发拒绝] ${d.error?.code || '错误'}: ${errMsg}`, 'var(--accent-red)');
      appendLog(`[下发拒绝] ${d.error?.code || '错误'}: ${errMsg}`);
      if (statusBadge) {
        statusBadge.className = 'ds-badge ds-badge-red';
        statusBadge.innerText = '下发被拒绝';
      }
    }
  } catch (e) {
    appendNavTaskLog(`[下发异常] ${e}`, 'var(--accent-red)');
    appendLog(`[下发异常] ${e}`);
  }
}

let activeNavPollInterval = null;
let lastInterruptedNavTask = null;

// 轮询追踪寻路任务状态 (高频动态监控流，单例防重)
async function trackNavTask(taskId) {
  if (activeNavPollInterval) {
    clearInterval(activeNavPollInterval);
    activeNavPollInterval = null;
  }
  const startTime = Date.now();
  let lastProgress = -1;
  activeNavPollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/v1/navigation/tasks/${taskId}`);
      if (!res.ok) {
        clearInterval(activeNavPollInterval);
        activeNavPollInterval = null;
        return;
      }
      const data = await res.json();
      const status = data.status;
      const statusBadge = document.getElementById('navLiveTaskStatusBadge');
      const progress = data.progress || {};
      const completed = progress.completed_steps ?? 0;
      const total = progress.total_steps ?? 0;
      const curPos = data.current?.position || data.current || {};
      const goalPos = data.goal?.position || data.goal || {};
      const curX = curPos.x ?? '-';
      const curY = curPos.y ?? '-';
      const curZ = curPos.z ?? '-';
      const goalX = goalPos.x ?? '-';
      const goalY = goalPos.y ?? '-';
      const goalZ = goalPos.z ?? '-';

      if (status === 'executing' || status === 'prechecking' || status === 'queued') {
        if (statusBadge) {
          statusBadge.className = 'ds-badge ds-badge-yellow';
          statusBadge.innerHTML = `<span class="ds-dot"></span>执行中 [${completed}/${total}步]`;
        }
        if (completed !== lastProgress) {
          lastProgress = completed;
          appendNavTaskLog(`[时序推进] 进度: ${completed}/${total}步 | 实时 RAM 坐标: (X=${curX}, Z=${curZ}, Y=${curY}) | 动作: ${data.movement_mode || 'run'}`, 'var(--text-1)');
        }
      } else if (status === 'succeeded' || status === 'completed') {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        appendNavTaskLog(`[✓ 任务完成] 角色已成功抵达目标坐标 (X=${goalX}, Z=${goalZ}, Y=${goalY})！总耗时: ${elapsed}s`, 'var(--accent-green)');
        appendLog(`[任务完成] 角色已成功抵达目标坐标！`);
        if (statusBadge) {
          statusBadge.className = 'ds-badge ds-badge-green';
          statusBadge.innerText = `✓ 已完成 (${total}步)`;
        }
        clearInterval(activeNavPollInterval);
        activeNavPollInterval = null;
        const btnCancel = document.getElementById('btnCancelNav');
        if (btnCancel) btnCancel.style.display = 'none';
        const btnResume = document.getElementById('btnResumeNav');
        const btnResumeMon = document.getElementById('btnResumeNavMonitor');
        if (btnResume) btnResume.style.display = 'none';
        if (btnResumeMon) btnResumeMon.style.display = 'none';
        pollPlayerRuntime();
        pollRadar();
      } else if (status === 'failed' || status === 'cancelled') {
        const stopReasonObj = data.stop_reason || {};
        const stopCode = stopReasonObj.code || '';
        const stopMsg = stopReasonObj.message || data.error?.message || data.reason || '无';
        const isBattle = stopCode.includes('BATTLE') || String(stopMsg).toLowerCase().includes('battle') || String(stopReasonObj.details?.reason || '').includes('battle');

        if (isBattle) {
          lastInterruptedNavTask = { taskId: taskId, goal: data.goal, movement_mode: data.movement_mode };
          appendNavTaskLog(`[⚠️ 战斗打断] 角色在移动途中遭遇野怪战斗！原导航终点 (X=${goalX}, Z=${goalZ}, Y=${goalY}) 已安全暂存。`, 'var(--accent-amber)');
          appendNavTaskLog(`[💡 战后恢复] 战斗结束（逃跑或获胜）返回大地图后，点击上方“[🔄 战后恢复导航 (/resume)]”即可一键自动继续赶路！`, 'var(--accent-green)');
          appendLog(`[战斗打断] 导航因野怪战斗暂停，原目标已暂存`);
          if (statusBadge) {
            statusBadge.className = 'ds-badge ds-badge-amber';
            statusBadge.innerHTML = '<span class="ds-dot"></span>⚠️ 战斗中断 (可恢复)';
          }
          const btnResume = document.getElementById('btnResumeNav');
          const btnResumeMon = document.getElementById('btnResumeNavMonitor');
          if (btnResume) btnResume.style.display = 'inline-flex';
          if (btnResumeMon) btnResumeMon.style.display = 'inline-flex';
        } else {
          appendNavTaskLog(`[✗ 任务终止] 状态: ${status}, 原因: ${stopMsg}`, 'var(--accent-red)');
          appendLog(`[任务终止] 状态: ${status}, 原因: ${stopMsg}`);
          if (statusBadge) {
            statusBadge.className = 'ds-badge ds-badge-red';
            statusBadge.innerText = `✗ ${status}: ${data.stop_reason?.code || '终止'}`;
          }
        }

        clearInterval(activeNavPollInterval);
        activeNavPollInterval = null;
        const btnCancel = document.getElementById('btnCancelNav');
        if (btnCancel) btnCancel.style.display = 'none';
        pollPlayerRuntime();
        pollRadar();
      }
    } catch (e) {
      if (activeNavPollInterval) {
        clearInterval(activeNavPollInterval);
        activeNavPollInterval = null;
      }
    }
  }, 400);
}

// 战后恢复导航 (调用 POST /api/v1/navigation/resume)
async function handleResumeNav() {
  appendNavTaskLog('[恢复请求] 正在调用 POST /api/v1/navigation/resume 恢复战后导航...', 'var(--accent-cyan)');
  appendLog('[恢复导航] 调用 POST /api/v1/navigation/resume 恢复战后导航...');
  try {
    const res = await fetch('/api/v1/navigation/resume', { method: 'POST' });
    const d = await res.json();
    if (res.ok && (d.task_id || d.id)) {
      activeNavTaskId = d.task_id || d.id;
      const totalSteps = d.progress?.total_steps || d.total_steps || (activePlannedPathMap.size > 0 ? activePlannedPathMap.size : '?');
      appendNavTaskLog(`[✓ 导航恢复成功] 新任务 ID: ${activeNavTaskId}, 重新规划步数: ${totalSteps}步, 模式: ${d.movement_mode || 'run'}`, 'var(--accent-green)');
      appendLog(`[导航恢复] 成功启动新任务 ${activeNavTaskId}`);
      const btnResume = document.getElementById('btnResumeNav');
      const btnResumeMon = document.getElementById('btnResumeNavMonitor');
      if (btnResume) btnResume.style.display = 'none';
      if (btnResumeMon) btnResumeMon.style.display = 'none';
      const btnCancel = document.getElementById('btnCancelNav');
      if (btnCancel) btnCancel.style.display = 'inline-flex';
      trackNavTask(activeNavTaskId);
    } else {
      const reason = d.detail?.reason || d.error?.code || d.detail?.message || d.detail || JSON.stringify(d);
      if (String(reason).includes('BATTLE') || String(reason).includes('battle')) {
        appendNavTaskLog(`[提示] 当前主角仍在对战中！请先在“战斗队伍”面板使用招式击败对手，或点击“[脱离战斗 (逃跑)]”，脱战返回大地图后点击即可继续！`, 'var(--accent-amber)');
      } else {
        appendNavTaskLog(`[恢复受阻] ${reason}`, 'var(--accent-red)');
      }
    }
  } catch (e) {
    appendNavTaskLog(`[恢复异常] ${e}`, 'var(--accent-red)');
  }
}

// 取消当前正在执行的寻路任务
async function handleCancelNav() {
  if (!activeNavTaskId) return;
  appendLog(`[取消任务] 调用 POST /api/v1/navigation/tasks/${activeNavTaskId}/cancel...`);
  try {
    const res = await fetch(`/api/v1/navigation/tasks/${activeNavTaskId}/cancel`, { method: 'POST' });
    const d = await res.json();
    appendLog(`[取消响应] ${JSON.stringify(d)}`);
    const btnCancel = document.getElementById('btnCancelNav');
    if (btnCancel) btnCancel.style.display = 'none';
  } catch (e) {
    appendLog(`[取消异常] ${e}`);
  }
}

// A* 寻路规划与全息解构
async function handlePlanNav() {
  const tx = parseInt(document.getElementById('navTargetX')?.value || String(livePlayerGrid.x), 10);
  const tz = parseInt(document.getElementById('navTargetZ')?.value || String(livePlayerGrid.z >= 2 ? livePlayerGrid.z - 2 : livePlayerGrid.z + 2), 10);
  const rawY = document.getElementById('navTargetY')?.value;
  const ty = (rawY !== undefined && rawY !== '') ? parseInt(rawY, 10) : (livePlayerGrid.y ?? 0);
  const mode = document.getElementById('navModeSelect')?.value || 'auto';

  appendLog(`[路径规划] 正在计算前往网格 (${tx}, ${tz}, 层高Y=${ty}) 模式=${mode} 的最优 A* 路径...`);

  const destPayload = {
    type: "grid",
    space: "gen5-field-grid-v1",
    zone_id: livePlayerZone || 457,
    x: tx,
    y: ty,
    z: tz
  };

  try {
    const res = await fetch('/api/v1/navigation/plans', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        destination: destPayload,
        movement_mode: mode
      })
    });
    const d = await res.json();
    if (res.ok) {
      appendLog(`[规划成功] 状态: ${d.status}, 选定模式: ${d.movement?.selected}, 步数: ${d.cost?.steps || 0}`);
      
      // 提取路径节点并在网格切片上点亮连通路径
      activePlannedPathMap.clear();
      activePlannedMovementMode = d.movement?.selected || mode || "run";
      const nodes = d.route_detail?.nodes || d.segments?.[0]?.path || [];
      nodes.forEach((n, idx) => {
        const isGoal = (idx === nodes.length - 1);
        const nodeY = (n.y !== undefined && n.y !== null) ? n.y : ty;
        const stepNum = idx; // 1st step along path is 1, 2nd is 2...
        if (idx > 0 || isGoal) {
          activePlannedPathMap.set(`${n.x},${n.z},${nodeY}`, { step: stepNum, total: nodes.length - 1, isGoal: isGoal, y: nodeY, isRealFloor: true });
          activePlannedPathMap.set(`${n.x},${n.z}`, { step: stepNum, total: nodes.length - 1, isGoal: isGoal, y: nodeY, isProjection: true });
        }
      });
      if (activeSlicesData) {
        renderMultiLayerGrid(activeSlicesData);
      }

      renderNavPlanDetail(d);
    } else {
      activePlannedPathMap.clear();
      if (activeSlicesData) {
        renderMultiLayerGrid(activeSlicesData);
      }
      renderNavPlanFailure(tx, ty, tz, d.error || {});
      appendNavTaskLog(`[规划受阻] 寻路未通过 (${d.error?.code || 'NAV_NO_ROUTE'}): ${d.error?.message || '目标不可达'}`, 'var(--accent-red)');
      appendLog(`[规划受阻] ${d.error?.code || '错误'}: ${d.error?.message || '不可达'}`);
      const obs = document.getElementById('navObstacleStatus');
      if (obs) {
        obs.innerText = `[寻路未通过: ${d.error?.code || 'NAV_NO_ROUTE'}] ${d.error?.message || '不可达'}`;
        obs.style.color = 'var(--accent-red)';
      }
    }
  } catch (e) {
    appendLog(`[规划异常] ${e}`);
  }
}

function renderNavPlanFailure(tx, ty, tz, error) {
  const startElem = document.getElementById('navStartCoord');
  if (startElem) startElem.innerText = `X=${livePlayerGrid.x}, Y=${livePlayerGrid.y ?? 0}, Z=${livePlayerGrid.z}`;
  
  const goalElem = document.getElementById('navGoalCoord');
  if (goalElem) goalElem.innerText = `X=${tx}, Y=${ty}, Z=${tz}`;

  const stepsElem = document.getElementById('navStepsVal');
  if (stepsElem) stepsElem.innerText = '0 步 (不可达)';
  
  const turnsElem = document.getElementById('navTurnsVal');
  if (turnsElem) turnsElem.innerText = '-';

  const timeElem = document.getElementById('navTimeVal');
  if (timeElem) timeElem.innerText = '不可达';

  const modeElem = document.getElementById('navSelectedMode');
  if (modeElem) modeElem.innerText = '[路径阻断 / 无法通行]';

  const reasonElem = document.getElementById('navModeReason');
  if (reasonElem) {
    const targetCell = activeCellDataMap.get(`${tx},${tz},${ty}`) || {};
    if (targetCell.symbol === '↕' || String(targetCell.kind || '').includes('悬空') || String(targetCell.kind || '').includes('open_air')) {
      reasonElem.innerHTML = `<span style="color:var(--accent-amber);">☁️ 悬空虚空阻断：目标坐标 (X=${tx}, Z=${tz}) 在高台标高 Y=+${ty} 处为高空敞空区（无桥面/路面支撑，不可站立）！下方地面标高 Y=0 处存在道路。若要前往地面，请将层高改为 0。</span>`;
    } else if (targetCell.symbol === '#' || targetCell.walkable === false) {
      reasonElem.innerHTML = `<span style="color:var(--accent-red);">🧱 实体障碍阻隔：目标地块 (X=${tx}, Z=${tz}, Y=${ty}) 为物理阻隔墙体或断崖，不可作为落脚点。</span>`;
    } else if ((livePlayerGrid.y ?? 0) !== ty) {
      reasonElem.innerText = `立体跨层阻断：主角当前在标高 Y=${livePlayerGrid.y ?? 0} (高架/特定层)，目标在标高 Y=${ty}，二者处于不同垂直层且无阶梯相连。请在上方 2D 雷达中选择同层绿色地块，或寻找楼梯转换层。`;
    } else {
      reasonElem.innerText = error.message || `障碍阻隔：目标地块或沿途路径存在物理墙体、断崖或动态 NPC 阻挡，无法生成连通 A* 路线。`;
    }
  }

  const obsElem = document.getElementById('navObstacleStatus');
  if (obsElem) {
    obsElem.innerText = `[寻路未通过: ${error.code || 'NAV_NO_ROUTE'}] ${error.message || '目标不可达'}`;
    obsElem.style.color = 'var(--accent-red)';
  }

  const actionListElem = document.getElementById('navActionList');
  if (actionListElem) {
    actionListElem.innerHTML = `<span style="color:var(--accent-red);">未生成动作流：${error.message || '目标坐标不可达，未下发任何移动按键。'}</span>`;
  }
}

function renderNavPlanDetail(data) {
  const start = data.resolved_start?.position || {};
  const goal = data.resolved_goal?.position || {};
  const steps = data.cost?.steps || 0;
  const turns = data.cost?.turns || 0;
  const movement = data.movement || {};
  const selectedMode = movement.selected || 'walk';
  const modeFpt = { 'bike': 4, 'run': 8, 'walk': 16, 'surf': 14 }[selectedMode] || 16;
  const estFrames = steps * modeFpt;
  const estSec = (estFrames / 60).toFixed(2);

  const modeZh = {
    'bike': '自行车极速模式 (Bike · 4帧/格)',
    'run': '连续奔跑模式 (Run · 8帧/格)',
    'walk': '常规安全步行 (Walk · 16帧/格)',
    'surf': '水路冲浪航行 (Surf · 14帧/格)'
  }[selectedMode] || selectedMode;

  const startElem = document.getElementById('navStartCoord');
  if (startElem) startElem.innerText = `X=${start.x}, Y=${start.y}, Z=${start.z}`;
  const goalElem = document.getElementById('navGoalCoord');
  if (goalElem) goalElem.innerText = `X=${goal.x}, Y=${goal.y}, Z=${goal.z}`;

  const stepsElem = document.getElementById('navStepsVal');
  if (stepsElem) stepsElem.innerText = `${steps} 步`;
  const turnsElem = document.getElementById('navTurnsVal');
  if (turnsElem) turnsElem.innerText = `${turns} 次`;
  const timeElem = document.getElementById('navTimeVal');
  if (timeElem) timeElem.innerText = `${estFrames} 帧 (约 ${estSec} 秒)`;

  const modeElem = document.getElementById('navSelectedMode');
  if (modeElem) modeElem.innerText = `[${modeZh}]`;

  const reasonElem = document.getElementById('navModeReason');
  if (reasonElem) {
    const rawReason = movement.reasons?.[selectedMode] || '满足最优速度判定条件';
    reasonElem.innerText = rawReason;
  }

  const obsElem = document.getElementById('navObstacleStatus');
  const constraints = data.navigation_constraints || [];
  if (obsElem) {
    if (constraints.length > 0) {
      obsElem.innerText = `[前瞻探测到 ${constraints.length} 个动态 NPC/障碍物已成功避让]`;
      obsElem.style.color = 'var(--accent-amber)';
    } else {
      obsElem.innerText = `[前瞻 3 格感知清晰 · 路径畅通无阻]`;
      obsElem.style.color = 'var(--accent-green)';
    }
  }

  const actions = data.route_detail?.actions || [];
  const actionListElem = document.getElementById('navActionList');
  if (actionListElem) {
    if (actions.length > 0) {
      actionListElem.innerHTML = actions.map((a, idx) => {
        const dirZh = { 'North': '向北', 'South': '向南', 'East': '向东', 'West': '向西' }[a.direction] || a.direction;
        const dur = a.steps * modeFpt;

        const modeInfo = {
          'bike': { name: '自行车极速', icon: '🚲', fpt: 4, style: 'background:#FF8F00; color:#000; border:1px solid #FFD54F;' },
          'run':  { name: '连续奔跑',   icon: '🏃', fpt: 8, style: 'background:#1B5E20; color:#E8F5E9; border:1px solid #00E676;' },
          'walk': { name: '常规步行',   icon: '🚶', fpt: 16, style: 'background:#009624; color:#FFFFFF; border:1px solid #00E676;' },
          'surf': { name: '冲浪水路',   icon: '🏄', fpt: 14, style: 'background:#0D47A1; color:#FFFFFF; border:1px solid #40C4FF;' }
        }[selectedMode] || { name: selectedMode, icon: '🚶', fpt: modeFpt, style: '' };

        let elevationBadge = '';
        if (a.from?.y !== undefined && a.to?.y !== undefined && a.from.y !== a.to.y) {
          if (a.to.y > a.from.y) {
            elevationBadge = `<span class="ds-badge" style="background:#00838F; color:#E0F7FA; border:1px solid #00E5FF; font-size:11px; padding:1px 6px;">▲ 楼梯爬升 Y=${a.from.y}➔Y=${a.to.y}</span>`;
          } else {
            elevationBadge = `<span class="ds-badge" style="background:#E65100; color:#FFF3E0; border:1px solid #FFB74D; font-size:11px; padding:1px 6px;">▼ 楼梯下行 Y=${a.from.y}➔Y=${a.to.y}</span>`;
          }
        }

        return `
          <div style="margin:4px 0; display:flex; align-items:center; gap:6px; flex-wrap:wrap; font-size:12px; line-height:1.7;">
            <strong style="color:var(--text-1); min-width:20px;">${idx + 1}.</strong>
            <span class="ds-badge" style="${modeInfo.style} font-size:11px; font-weight:700; padding:1px 6px;">
              ${modeInfo.icon} ${modeInfo.name} · ${modeInfo.fpt}帧/格
            </span>
            <strong style="color:var(--accent-cyan);">[${dirZh}直行 ${a.steps} 格]</strong>
            ${elevationBadge}
            <span style="font-family:var(--font-mono); color:var(--text-2);">
              从 (${a.from?.x}, ${a.from?.y}, ${a.from?.z}) ➔ (${a.to?.x}, ${a.to?.y}, ${a.to?.z})
            </span>
            <span style="color:var(--text-3); font-size:11px;">
              · 持续按键 ${dur} 帧 · 实时 RAM 坐标制动
            </span>
          </div>
        `;
      }).join('');
    } else {
      actionListElem.innerText = '原地或同一瓦片，无需输入动作。';
    }
  }
}

async function handleDirectInput(button) {
  const btnZh = {
    'Up': '十字键上', 'Down': '十字键下', 'Left': '十字键左', 'Right': '十字键右',
    'A': '按键 A', 'B': '按键 B', 'X': '按键 X', 'Y': '按键 Y',
    'Start': '开始键 START', 'Select': '选择键 SELECT'
  };
  appendLog(`[手柄输入] 正在下发 '${btnZh[button] || button}' 按键 (调用 POST /api/actions/press)...`);
  try {
    const res = await fetch('/api/actions/press', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ button: button, frames: 4 })
    });
    const d = await res.json();
    const iv = d.result?.input_verification || {};
    appendLog(`[按键成功] 按键: ${btnZh[button] || button}, 接收: ${iv.accepted}, 帧变化: ${iv.sent_frame} -> ${iv.frame_after}, 队列归零: ${iv.completed}`);
  } catch (e) {
    appendLog(`[按键异常] ${e}`);
  }
}

// 定时轮询驱动与 URL Hash 恢复
function initV2() {
  const hash = window.location.hash.replace('#', '');
  if (hash && ['overview', 'radar', 'combat', 'roster', 'memory', '3d', 'pc'].includes(hash)) {
    switchTab("pane-" + hash);
  }

  pollPlayerRuntime().finally(() => {
    pollRadar();
  });
  pollParty();
  pollInventory();
  pollBattleState();
  pollStoryProgression();

  setInterval(pollPlayerRuntime, 2000);
  setInterval(pollRadar, 1500);
  setInterval(pollBattleState, 3000);
  setInterval(pollStoryProgression, 5000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initV2);
} else {
  initV2();
}

window.addEventListener('hashchange', () => {
  const hash = window.location.hash.replace('#', '');
  if (hash && ['overview', 'radar', 'combat', 'roster', 'memory', '3d', 'pc'].includes(hash)) {
    switchTab("pane-" + hash);
  }
});

window.addEventListener('keydown', (e) => {
  if (e.key === '`' || e.key === '~') {
    if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
      e.preventDefault();
      toggleGlobalLogDrawer();
    }
  }
});

let currentPocketFilter = 'all';
function filterInventoryByPocket(pkt) {
  currentPocketFilter = pkt;
  document.querySelectorAll('#pocketFilterButtons button').forEach(b => {
    b.className = 'ds-btn ds-btn-sm ds-btn-ghost';
  });
  if (event && event.target) {
    event.target.className = 'ds-btn ds-btn-sm ds-btn-primary';
  }
  if (pkt === 'all') {
    renderInventoryTable(cachedInventory);
    return;
  }
  const pocketMap = {
    'items': ['常规道具', '道具', 'items'],
    'medicine': ['回复药品', '药品', 'medicine'],
    'machines': ['技能机', '招式学习器', 'machines'],
    'key_items': ['重要道具', '关键道具', 'key_items'],
    'berries': ['树果', '浆果', 'berries']
  };
  const validPockets = pocketMap[pkt] || [pkt];
  const filtered = cachedInventory.filter(i => validPockets.some(vp => i.pocket.toLowerCase().includes(vp.toLowerCase())));
  renderInventoryTable(filtered);
}


// ============================================================================
// 宝可梦电脑仓储系统前端控制器 (PC Storage System Controller)
// ============================================================================
let currentActivePcBox = 1;
let cachedPcSummary = null;
let cachedCurrentBoxData = null;
let selectedPcMon = null;

async function pollPcStorage() {
  try {
    const res = await fetch('/api/v1/pokemon/pc/summary');
    if (!res.ok) return;
    const summary = await res.json();
    cachedPcSummary = summary;
    renderPcSummaryBadge(summary);
    renderPcBoxList(summary);
    await selectPcBox(currentActivePcBox);
  } catch (e) {
    console.error("pollPcStorage failed:", e);
  }
}

function renderPcSummaryBadge(summary) {
  const badge = document.getElementById('pcTotalBadge');
  if (badge) {
    badge.innerText = `仓储: ${summary.total_stored || 0} / ${summary.total_capacity || 720} (空余 ${summary.total_free || 720})`;
  }
}

function renderPcBoxList(summary) {
  const container = document.getElementById('pcBoxListContainer');
  if (!container) return;

  const boxes = summary.boxes || [];
  container.innerHTML = boxes.map(b => {
    const isActive = (b.box_id === currentActivePcBox);
    const hasMon = (b.count > 0);
    const cls = isActive ? 'ds-btn-cyan' : (hasMon ? 'ds-btn-yellow' : 'ds-btn-ghost');
    return `
      <button class="ds-btn ds-btn-sm ${cls}" style="padding:6px 2px; font-size:11px; display:flex; flex-direction:column; align-items:center; gap:2px;" onclick="selectPcBox(${b.box_id})">
        <span style="font-weight:700;">Box ${b.box_id}</span>
        <span style="font-size:10px; opacity:0.8;">${b.count}/30</span>
      </button>
    `;
  }).join('');
}

async function selectPcBox(boxId) {
  currentActivePcBox = boxId;
  const indicator = document.getElementById('activeBoxIndicator');
  if (indicator) indicator.innerText = `当前: Box ${boxId}`;

  const title = document.getElementById('pcCurrentBoxTitle');
  if (title) title.innerText = `📦 Box ${boxId} (30 槽位平面)`;

  if (cachedPcSummary) renderPcBoxList(cachedPcSummary);

  try {
    const res = await fetch(`/api/v1/pokemon/pc/box/${boxId}`);
    if (!res.ok) return;
    const data = await res.json();
    cachedCurrentBoxData = data;
    renderPcSlots(data);
  } catch (e) {
    console.error("selectPcBox failed:", e);
  }
}

function renderPcSlots(boxData) {
  const container = document.getElementById('pcSlotsGrid');
  if (!container) return;

  const slots = boxData.slots || [];
  container.innerHTML = slots.map(s => {
    if (s.empty) {
      return `
        <div style="min-height:68px; border:1px dashed var(--border-subtle); border-radius:4px; padding:6px; display:flex; flex-direction:column; justify-content:center; align-items:center; color:var(--text-3); font-size:11px; background:rgba(255,255,255,0.01);">
          <span>#${s.slot}</span>
          <span style="opacity:0.6;">(空位)</span>
        </div>
      `;
    }
    const isSelected = (selectedPcMon && selectedPcMon.box_id === s.box_id && selectedPcMon.slot === s.slot);
    const borderCls = isSelected ? 'border:2px solid var(--accent-cyan); background:rgba(0,229,255,0.1);' : 'border:1px solid var(--border-subtle); background:var(--bg-2);';
    return `
      <div style="min-height:68px; ${borderCls} border-radius:4px; padding:6px; cursor:pointer; display:flex; flex-direction:column; gap:2px; transition:all 0.12s ease;" onclick="inspectPcMon(${s.box_id}, ${s.slot})">
        <div style="display:flex; justify-content:space-between; font-size:11px;">
          <strong style="color:var(--text-1);">${s.species_name}</strong>
          <span style="color:var(--accent-amber); font-weight:700;">Lv.${s.level}</span>
        </div>
        <div style="font-size:10px; color:var(--text-2);">${s.ability_name}</div>
        <div style="font-size:9px; color:var(--accent-cyan); margin-top:auto;">${s.held_item_name !== '无携带' ? '🎁 ' + s.held_item_name : ''}</div>
      </div>
    `;
  }).join('');
}

function inspectPcMon(boxId, slotId) {
  if (!cachedCurrentBoxData) return;
  const mon = cachedCurrentBoxData.slots?.find(s => s.slot === slotId && !s.empty);
  if (!mon) return;

  selectedPcMon = mon;
  renderPcSlots(cachedCurrentBoxData);

  const card = document.getElementById('pcSelectedMonCard');
  if (!card) return;
  card.style.display = 'flex';
  card.style.flexDirection = 'column';
  card.style.gap = '10px';

  const typesStr = mon.types?.map(t => `<span class="ds-badge ds-badge-cyan">${t.name}</span>`).join(' ') || '';
  const movesStr = mon.moves?.map(m => m.name ? `<span class="ds-badge" style="background:var(--bg-2);">${m.name} (${m.current_pp}/${m.max_pp || '-'})</span>` : '').join(' ');
  const ivsStr = Object.entries(mon.ivs || {}).map(([k, v]) => `${k.toUpperCase()}:${v}`).join(' · ');
  const evsStr = Object.entries(mon.evs || {}).map(([k, v]) => `${k.toUpperCase()}:${v}`).join(' · ');

  card.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--border-subtle); padding-bottom:8px;">
      <div style="display:flex; align-items:center; gap:8px;">
        <span class="ds-badge ds-badge-yellow" style="font-size:13px; font-weight:800;">Box ${boxId} · 槽位 #${slotId}</span>
        <strong style="font-size:15px; color:var(--text-1);">${mon.species_name} (${mon.species_name_en})</strong>
        <span class="ds-badge ds-badge-amber">Lv.${mon.level}</span>
        ${typesStr}
        ${mon.is_shiny ? '<span class="ds-badge ds-badge-yellow">★ 闪光</span>' : ''}
      </div>
      <button class="ds-btn ds-btn-sm ds-btn-primary" onclick="handleWithdrawMon(${boxId}, ${slotId})">[取出到队伍 (Withdraw)]</button>
    </div>
    <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:10px; font-size:12px; line-height:1.7;">
      <div>
        <div style="color:var(--text-3); font-weight:700;">基础档案</div>
        <div>特性: <strong style="color:var(--text-1);">${mon.ability_name}</strong> ${mon.hidden_ability ? '(隐藏特性)' : ''}</div>
        <div>性格: <strong style="color:var(--text-1);">${mon.nature?.name}</strong> (${mon.nature?.name_en})</div>
        <div>携带道具: <strong style="color:var(--accent-amber);">${mon.held_item_name}</strong></div>
        <div>性别: ${mon.gender === 'M' ? '♂ 雄性' : mon.gender === 'F' ? '♀ 雌性' : '无性别'}</div>
      </div>
      <div>
        <div style="color:var(--text-3); font-weight:700;">个体值 (IVs) 与努力值 (EVs)</div>
        <div style="color:var(--text-2); font-family:var(--font-mono); font-size:11px;">IVs: ${ivsStr}</div>
        <div style="color:var(--text-2); font-family:var(--font-mono); font-size:11px;">EVs: ${evsStr}</div>
        <div>经验值: <span style="font-family:var(--font-mono);">${mon.experience}</span></div>
      </div>
      <div>
        <div style="color:var(--text-3); font-weight:700;">技能槽位 (Moves)</div>
        <div style="display:flex; flex-wrap:wrap; gap:4px; margin-top:4px;">
          ${movesStr || '<span style="color:var(--text-3);">无技能数据</span>'}
        </div>
      </div>
    </div>
  `;
}

async function handleWithdrawMon(boxId, slotId) {
  try {
    const res = await fetch('/api/v1/pokemon/pc/withdraw', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ box_id: boxId, box_slot: slotId })
    });
    const d = await res.json();
    if (!res.ok) {
      alert("取出失败: " + (d.detail || JSON.stringify(d)));
      return;
    }
    appendLog(`[PC取出成功] 已将 ${d.pokemon?.species_name} 从 Box ${boxId} 槽位 #${slotId} 取出到队伍 #${d.to_party_slot}！当前全队: ${d.new_party_count}/6`);
    pollPcStorage();
    pollPartyState();
  } catch (e) {
    alert("请求异常: " + e.message);
  }
}

async function handleOpenDepositModal() {
  try {
    const res = await fetch('/api/v1/game/party');
    if (!res.ok) return;
    const p = await res.json();
    const slots = p.slots || [];
    if (slots.length <= 1) {
      alert("队伍中只有 1 只宝可梦，按照游戏规则不可存入电脑！");
      return;
    }
    const chooseStr = slots.map(s => `[${s.slot}] ${s.species_name} Lv.${s.level}`).join('\n');
    const inputSlot = prompt(`请选择要存入电脑的队伍槽位编号 (2~${slots.length}):\n${chooseStr}`, "2");
    if (!inputSlot) return;
    const slotNum = parseInt(inputSlot, 10);
    if (slotNum < 1 || slotNum > slots.length) return;

    const depRes = await fetch('/api/v1/pokemon/pc/deposit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        party_slot: slotNum,
        target_box: currentActivePcBox
      })
    });
    const depData = await depRes.json();
    if (!depRes.ok) {
      alert("存入失败: " + (depData.detail || JSON.stringify(depData)));
      return;
    }
    appendLog(`[PC存入成功] 已将队伍 #${slotNum} ${depData.pokemon?.species_name} 存入 Box ${depData.to_box} 槽位 #${depData.to_slot}！`);
    pollPcStorage();
    pollPartyState();
  } catch (e) {
    alert("存入异常: " + e.message);
  }
}

async function handlePcSearch() {
  const q = document.getElementById('pcSearchInput')?.value?.trim();
  if (!q) return;

  try {
    const res = await fetch(`/api/v1/pokemon/pc/search?species_name=${encodeURIComponent(q)}`);
    if (!res.ok) return;
    const d = await res.json();
    const count = d.match_count || 0;
    const rEl = document.getElementById('pcSearchResults');
    if (rEl) {
      rEl.innerText = `找到 ${count} 只匹配宝可梦`;
    }
    if (count > 0 && d.results?.[0]) {
      const first = d.results[0];
      await selectPcBox(first.box_id);
      inspectPcMon(first.box_id, first.slot);
    }
  } catch (e) {
    console.error("handlePcSearch failed:", e);
  }
}

function resetPcSearch() {
  const input = document.getElementById('pcSearchInput');
  if (input) input.value = '';
  const rEl = document.getElementById('pcSearchResults');
  if (rEl) rEl.innerText = '';
  selectPcBox(1);
}

async function handleExecuteFly() {
  const destVal = document.getElementById('flyDestinationSelect')?.value || '120';
  const destName = document.getElementById('flyDestinationSelect')?.selectedOptions[0]?.text || destVal;
  appendLog(`[飞翔指令] 准备启航飞翔前往 ${destName} (POST /api/v1/navigation/fast-travel/fly)...`);
  appendNavTaskLog(`[飞翔启航] 正在通过 6号位叉字蝠 (Fly #19) 跨区飞翔前往 ${destName}...`, 'var(--accent-cyan)');
  try {
    const res = await fetch('/api/v1/navigation/fast-travel/fly', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ destination: parseInt(destVal, 10), method: 'auto' })
    });
    const d = await res.json();
    if (res.ok) {
      const depName = d.departure?.zone_id ? `Zone ${d.departure.zone_id}` : '当前区域';
      const arrName = d.destination?.name || `Zone ${d.destination?.zone_id}`;
      const grid = d.destination?.landing_grid ? `(${d.destination.landing_grid.x}, ${d.destination.landing_grid.z})` : '';
      appendLog(`[飞翔成功] 航程完成！从 ${depName} ➔ 成功抵达【${arrName}】门垫 ${grid}`);
      appendLog(`[飞翔凭证] 执飞机体: 席位${d.fly_pokemon?.slot}【${d.fly_pokemon?.species_name} Lv.${d.fly_pokemon?.level}】· 喷射徽章许可已核验`);
      appendNavTaskLog(`[飞翔着陆] 成功降落【${arrName}】宝可梦中心门外门垫 ${grid}！`, 'var(--accent-green)');
      pollPlayerRuntime();
      pollRadar();
    } else {
      appendLog(`[飞翔拒绝] ${d.detail?.message || JSON.stringify(d)}`);
      appendNavTaskLog(`[飞翔拒绝] ${d.detail?.message || '未满足起飞条件'}`, 'var(--accent-red)');
    }
  } catch (e) {
    appendLog(`[飞翔异常] ${e}`);
    appendNavTaskLog(`[飞翔异常] ${e}`, 'var(--accent-red)');
  }
}

async function handleEvaluateFly() {
  appendLog('[飞翔评估] 正在校验当前环境与队伍飞翔资质 (GET /api/v1/navigation/fast-travel/evaluate)...');
  try {
    const res = await fetch('/api/v1/navigation/fast-travel/evaluate');
    const d = await res.json();
    appendLog(`[评估结果] 合法起飞: ${d.legal} | 持有飞翔技能: ${d.has_move_fly} | 区域允许: ${d.current_zone_allows_fly}`);
    appendLog(`[评估依据] ${d.reason} (合众可用降落城镇数: ${d.available_destinations_count})`);
    const badge = document.getElementById('flyStatusBadge');
    if (badge) {
      if (d.legal) {
        badge.innerText = '🦅 飞翔就绪 · 58城镇全域通达';
        badge.className = 'ds-badge ds-badge-cyan';
      } else {
        badge.innerText = '🚫 当前禁止飞翔';
        badge.className = 'ds-badge ds-badge-rose';
      }
    }
  } catch (e) {
    appendLog(`[评估异常] ${e}`);
  }
}
