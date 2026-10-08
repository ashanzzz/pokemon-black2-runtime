with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Update isStair rendering in renderMultiLayerGrid:
old_stair_grid = """        } else if (isStair) {
          sym = rawSym;
          cellClass = 'cell-stair';"""

new_stair_grid = """        } else if (isStair) {
          // 立体双向符号法：在地面(Y=0)显示上行▲，在高台(Y=2)显示下行▼，直观呈现上下接驳
          sym = (fy >= 2) ? '▼' : '▲';
          cellClass = (fy >= 2) ? 'cell-stair cell-stair-down' : 'cell-stair cell-stair-up';"""
text = text.replace(old_stair_grid, new_stair_grid)

# 2. Update renderTileInspection for isStair
old_stair_mat = """    } else if (hasWarp) {
      matElem.innerHTML = `<strong style="color:var(--accent-red);">[D 传送大门]</strong> 材质: ${matKind} · 跨区传送门垫 (TileClass: ${tileClassHex})`;"""

new_stair_mat = """    } else if (isStair) {
      const isUp = (y <= 0);
      const stairSym = isUp ? '▲' : '▼';
      const stairLabel = isUp ? '上行爬升阶梯' : '下行降落阶梯';
      matElem.innerHTML = `<strong style="color:var(--accent-cyan);">[${stairSym} ${stairLabel}]</strong> 材质: staircase_slope · 跨层立体阶梯 (TileClass: ${tileClassHex})`;
    } else if (hasWarp) {
      matElem.innerHTML = `<strong style="color:var(--accent-red);">[D 传送大门]</strong> 材质: ${matKind} · 跨区传送门垫 (TileClass: ${tileClassHex})`;"""
text = text.replace(old_stair_mat, new_stair_mat)

old_stair_desc = """    } else if (hasWarp && isPlayerStandingHere) {
      descElem.innerHTML = `<span style="color:var(--accent-red); font-weight:700;">🚪 主角当前正站在跨区传送门垫 [D] 上！踏入或向此方向移动将触发地图黑屏转场。</span>`;"""

new_stair_desc = """    } else if (isStair) {
      const isUp = (y <= 0);
      descElem.innerHTML = isUp
        ? `<span style="color:var(--accent-cyan); font-weight:700;">⛰️ 跨层上行阶梯通道 (▲)：</span>连接地面 Y=0 ➔ 高台 Y=+2。在此处向西直行即可安全爬升至高台平台 (X=10, Y=2)。南北两侧带有防护栏，无法侧向掉落。`
        : `<span style="color:var(--accent-amber); font-weight:700;">⛰️ 跨层下行阶梯通道 (▼)：</span>连接高台 Y=+2 ➔ 地面 Y=0。在此处向东直行即可安全走下阶梯返回地面道路。南北两侧带有防护栏。`;
    } else if (hasWarp && isPlayerStandingHere) {
      descElem.innerHTML = `<span style="color:var(--accent-red); font-weight:700;">🚪 主角当前正站在跨区传送门垫 [D] 上！踏入或向此方向移动将触发地图黑屏转场。</span>`;"""
text = text.replace(old_stair_desc, new_stair_desc)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js updated with directional staircase rendering (▲ on Y=0, ▼ on Y=2)!")
