with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Fix sym assignment in renderMultiLayerGrid:
# Replace:
#   const isCatwalk = ['╫', '╪', '↕', 'o'].includes(rawSym);
#   ...
#   } else if (isCatwalk) {
#     sym = '╫';
#     cellClass = 'cell-catwalk';
#   }
old_catwalk_render = """        const isCatwalk = ['╫', '╪', '↕', 'o'].includes(rawSym);
        const isStair = ['▲', '▼'].includes(rawSym);
        const isNPC = (rawSym === 'N' || rawSym === 'T');
        const isItem = (rawSym === 'h' || rawSym === 'I');

        // 优先级严格保障：主角 > 传送大门[D] > 楼梯 > 独木桥 > NPC > 道具 > 草丛/水体 > 物理墙体 > 普通平地
        if (cell.is_player || rawSym === 'P') {
          sym = 'P';
          cellClass = isWarpTile ? 'cell-P cell-warp-player' : 'cell-P';
        } else if (rawSym === 'D' || cell.is_portal_doorway === true) {
          sym = 'D';
          cellClass = 'cell-warp';
        } else if (isStair) {
          sym = rawSym;
          cellClass = 'cell-stair';
        } else if (isCatwalk) {
          sym = '╫';
          cellClass = 'cell-catwalk';
        }"""

new_catwalk_render = """        const isCatwalk = ['╫', '╪', 'o'].includes(rawSym);
        const isStair = ['▲', '▼'].includes(rawSym);
        const isNPC = (rawSym === 'N' || rawSym === 'T');
        const isItem = (rawSym === 'h' || rawSym === 'I');

        // 优先级严格保障：主角 > 传送大门[D] > 楼梯 > 独木桥入口[╪]/主体[╫] > 悬空高低差[↕] > NPC > 道具 > 草丛/水体 > 物理墙体 > 普通平地
        if (cell.is_player || rawSym === 'P') {
          sym = 'P';
          cellClass = isWarpTile ? 'cell-P cell-warp-player' : 'cell-P';
        } else if (rawSym === 'D' || cell.is_portal_doorway === true) {
          sym = 'D';
          cellClass = 'cell-warp';
        } else if (isStair) {
          sym = rawSym;
          cellClass = 'cell-stair';
        } else if (isCatwalk) {
          sym = rawSym; // 严格保留 ╪ (入口) 与 ╫ (主体) 区分，契合 AGENTS.md 准则 7
          cellClass = 'cell-catwalk';
        } else if (rawSym === '↕') {
          sym = '↕';
          cellClass = 'cell-elevation-gap';
        }"""
text = text.replace(old_catwalk_render, new_catwalk_render)

# 2. Fix renderTileInspection for Air Gap (↕) and Catwalk Entry (╪) / Body (╫)
old_inspection_block = """  if (matElem) {
    if (hasWarp) {
      matElem.innerHTML = `<strong style="color:var(--accent-red);">[D 传送大门]</strong> 材质: ${matKind} · 跨区传送门垫 (TileClass: ${tileClassHex})`;
    } else if (isPlayerStandingHere) {
      matElem.innerHTML = `<strong style="color:var(--accent-cyan);">[P 主角站立点]</strong> 材质: ${matKind} · ${matLabel} (TileClass: ${tileClassHex})`;
    } else {
      matElem.innerText = `材质: ${matKind} · ${matLabel} (TileClass: ${tileClassHex})`;
    }
  }

  if (descElem) {
    if (hasWarp && isPlayerStandingHere) {
      descElem.innerHTML = `<span style="color:var(--accent-red); font-weight:700;">🚪 主角当前正站在跨区传送门垫 [D] 上！踏入或向此方向移动将触发地图黑屏转场。</span>`;
    } else if (hasWarp) {
      descElem.innerHTML = `<span style="color:var(--accent-red); font-weight:700;">🚪 传送大门出入口 [D]：ROM 预设跨区传送门垫。踏入将直接传送至外部相邻地图。</span>`;
    } else if (isPlayerStandingHere) {
      // 检查正前方相邻是否是门
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
  }"""

new_inspection_block = """  const isAirGap = (symbol === '↕' || (!walkable && cell.alternate_layer_available));
  const isCatwalkEntry = (symbol === '╪' || tileClass === 191 || matKind === 'catwalk_entry');
  const isCatwalkBody = (symbol === '╫' || tileClass === 190 || matKind === 'catwalk');

  if (matElem) {
    if (isAirGap) {
      matElem.innerHTML = `<strong style="color:var(--text-3);">[☁️ 空中敞空 / 悬空空间]</strong> 材质: open_air · 无上层路面 (空中悬空)`;
    } else if (isCatwalkEntry) {
      matElem.innerHTML = `<strong style="color:var(--accent-amber);">[╪ 独木桥入口]</strong> 材质: catwalk_entry · 独木桥桥头入口 (TileClass: ${tileClassHex})`;
    } else if (isCatwalkBody) {
      matElem.innerHTML = `<strong style="color:var(--accent-amber);">[╫ 独木桥主体]</strong> 材质: catwalk · 独木桥主体 (TileClass: ${tileClassHex})`;
    } else if (hasWarp) {
      matElem.innerHTML = `<strong style="color:var(--accent-red);">[D 传送大门]</strong> 材质: ${matKind} · 跨区传送门垫 (TileClass: ${tileClassHex})`;
    } else if (isPlayerStandingHere) {
      matElem.innerHTML = `<strong style="color:var(--accent-cyan);">[P 主角站立点]</strong> 材质: ${matKind} · ${matLabel} (TileClass: ${tileClassHex})`;
    } else {
      matElem.innerText = `材质: ${matKind} · ${matLabel} (TileClass: ${tileClassHex})`;
    }
  }

  if (descElem) {
    if (isAirGap) {
      const lowerY = cell.alternate_layer_y !== undefined ? cell.alternate_layer_y : 0;
      descElem.innerHTML = `<span style="color:var(--text-2);">☁️ 标高 Y=${y >= 0 ? '+' + y : y} 处为高空敞空区（无高架或独木桥支撑，无法立足或随意跳下）。下方地面投影为: <strong>${matKind} · ${matLabel} (标高 Y=${lowerY >= 0 ? '+' + lowerY : lowerY})</strong>。如需上行，请通过阶梯通道爬升。</span>`;
    } else if (isCatwalkEntry) {
      descElem.innerHTML = `<span style="color:var(--accent-amber); font-weight:700;">🌉 独木桥桥头入口 (Catwalk Entry · 0x00BF)：</span>高台与独木桥主体的过渡端点。踏入后角色将进入独木桥平衡状态。主轴向可通行，侧向为悬空边缘，禁止自行车骑行。`;
    } else if (isCatwalkBody) {
      descElem.innerHTML = `<span style="color:var(--accent-amber); font-weight:700;">🌉 独木桥主体 (Catwalk · 0x00BE)：</span>高空狭窄木桥，仅限主轴方向通行，南北两侧为高空坠落边缘。支持步行与跑步平衡通过，自行车严禁驶入。`;
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
  }"""
text = text.replace(old_inspection_block, new_inspection_block)

# 3. Update movement matrix when isAirGap or isCatwalk
old_matrix_block = """  // 全载具与步态许可矩阵 (Walk, Run, Bike, Surf)
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
  }"""

new_matrix_block = """  // 全载具与步态许可矩阵 (Walk, Run, Bike, Surf)
  const isWater = (symbol === 'W' || symbol === '~' || matKind.includes('water') || tileClass === 16 || tileClass === 17);
  const isTrueCatwalk = (symbol === '╫' || symbol === '╪' || matKind.includes('catwalk') || tileClass === 190 || tileClass === 191);
  const isStair = (['▲', '▼'].includes(symbol) || matKind.includes('stair'));

  // 1. 步行 (Walk)
  const mWalk = document.getElementById('matrixWalk');
  if (mWalk) {
    if (isAirGap) {
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
    if (isAirGap) {
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
    if (isAirGap) {
      mBike.className = 'ds-badge ds-badge-red';
      mBike.innerText = '🚲 自行车: [NO 悬空无法骑行]';
    } else if (isTrueCatwalk) {
      mBike.className = 'ds-badge ds-badge-amber';
      mBike.innerText = '🚲 自行车: [NO 独木桥狭窄阻断]';
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
  }"""
text = text.replace(old_matrix_block, new_matrix_block)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js updated: Air Gap properly shown as Open Air, catwalk entry ╪ vs body ╫ preserved!")
