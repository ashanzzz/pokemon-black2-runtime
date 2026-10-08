with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

old_airgap_code = """  const isAirGap = (symbol === '↕' || (!walkable && cell.alternate_layer_available));
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
    } else if (isCatwalkEntry) {"""

new_airgap_code = """  const altY = (cell.alternate_layer_y !== undefined && cell.alternate_layer_y !== null) ? cell.alternate_layer_y : (y > 0 ? 0 : 2);
  const isAltHigher = (altY > y); // 另一层在上方 (如当前 y=0, 上方 altY=2) -> 高台支架 / 实体墙基
  const isAltLower = (altY < y);  // 另一层在下方 (如当前 y=2, 下方 altY=0) -> 高空敞空 / 悬空空间

  const isPlatformBase = (!walkable && cell.alternate_layer_available && isAltHigher);
  const isAirGap = (!walkable && (symbol === '↕' || cell.alternate_layer_available) && isAltLower);
  const isCatwalkEntry = (symbol === '╪' || tileClass === 191 || matKind === 'catwalk_entry');
  const isCatwalkBody = (symbol === '╫' || tileClass === 190 || matKind === 'catwalk');

  if (matElem) {
    if (isPlatformBase) {
      matElem.innerHTML = `<strong style="color:var(--accent-red);">[🧱 高台支架 / 实体墙基]</strong> 材质: platform_support · 高架支撑立柱/桥墩 (上方 Y=+${altY} 为高台平台)`;
    } else if (isAirGap) {
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
    if (isPlatformBase) {
      descElem.innerHTML = `<span style="color:var(--text-2);">🧱 标高 Y=${y >= 0 ? '+' + y : y} 处为上方高台的<strong>实体支撑立柱/墙基支架（不可穿越）</strong>。其正上方标高 Y=+${altY} 处为可通行高台平台（<strong>${matKind} · ${matLabel}</strong>）。在当前地面层，此处为阻隔墙体；如需登上高台，请经由旁边的阶梯走廊爬升。</span>`;
    } else if (isAirGap) {
      descElem.innerHTML = `<span style="color:var(--text-2);">☁️ 标高 Y=${y >= 0 ? '+' + y : y} 处为高空敞空区（无高架或独木桥支撑，无法立足或随意跳下）。下方地面投影为: <strong>${matKind} · ${matLabel} (标高 Y=${altY >= 0 ? '+' + altY : altY})</strong>。如需下行，请通过阶梯通道返回地面。</span>`;
    } else if (isCatwalkEntry) {"""
text = text.replace(old_airgap_code, new_airgap_code)

# Update statusElem and matrixWalk/Run/Bike for isPlatformBase
old_matrix_branch = """  // 1. 步行 (Walk)
  const mWalk = document.getElementById('matrixWalk');
  if (mWalk) {
    if (isAirGap) {
      mWalk.className = 'ds-badge ds-badge-red';
      mWalk.innerText = '🚶 步行: [NO 悬空无法立足]';
    } else if (walkable && !isWater) {"""

new_matrix_branch = """  // 1. 步行 (Walk)
  const mWalk = document.getElementById('matrixWalk');
  if (mWalk) {
    if (isPlatformBase) {
      mWalk.className = 'ds-badge ds-badge-red';
      mWalk.innerText = '🚶 步行: [NO 支架实体阻隔]';
    } else if (isAirGap) {
      mWalk.className = 'ds-badge ds-badge-red';
      mWalk.innerText = '🚶 步行: [NO 悬空无法立足]';
    } else if (walkable && !isWater) {"""
text = text.replace(old_matrix_branch, new_matrix_branch)

old_run_branch = """  // 2. 奔跑 (Run)
  const mRun = document.getElementById('matrixRun');
  if (mRun) {
    if (isAirGap) {
      mRun.className = 'ds-badge ds-badge-red';
      mRun.innerText = '🏃 奔跑: [NO 悬空无法奔跑]';
    } else if (walkable && !isWater) {"""

new_run_branch = """  // 2. 奔跑 (Run)
  const mRun = document.getElementById('matrixRun');
  if (mRun) {
    if (isPlatformBase) {
      mRun.className = 'ds-badge ds-badge-red';
      mRun.innerText = '🏃 奔跑: [NO 支架实体阻隔]';
    } else if (isAirGap) {
      mRun.className = 'ds-badge ds-badge-red';
      mRun.innerText = '🏃 奔跑: [NO 悬空无法奔跑]';
    } else if (walkable && !isWater) {"""
text = text.replace(old_run_branch, new_run_branch)

old_bike_branch = """  // 3. 自行车 (Bike - 用户指定黄色)
  const mBike = document.getElementById('matrixBike');
  if (mBike) {
    if (isAirGap) {
      mBike.className = 'ds-badge ds-badge-red';
      mBike.innerText = '🚲 自行车: [NO 悬空无法骑行]';
    } else if (isTrueCatwalk) {"""

new_bike_branch = """  // 3. 自行车 (Bike - 用户指定黄色)
  const mBike = document.getElementById('matrixBike');
  if (mBike) {
    if (isPlatformBase) {
      mBike.className = 'ds-badge ds-badge-red';
      mBike.innerText = '🚲 自行车: [NO 支架实体阻隔]';
    } else if (isAirGap) {
      mBike.className = 'ds-badge ds-badge-red';
      mBike.innerText = '🚲 自行车: [NO 悬空无法骑行]';
    } else if (isTrueCatwalk) {"""
text = text.replace(old_bike_branch, new_bike_branch)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js updated: isPlatformBase (支架/墙基) vs isAirGap (高空悬空) strictly distinguished!")
