with open('frontend/v2.js', 'r', encoding='utf-8') as f:
    code = f.read()

# Fix summary badge and floor title with proper UTF-8 and stair_runtime support
old_block = """  // 楼层概要徽章提示
  const summaryBadge = document.getElementById('layerSummaryBadge');
  if (summaryBadge) {
    if (totalLayers === 1) {
      summaryBadge.innerText = `当前区域为单层地面 (标高 Y=${activeLayers[0]})`;
    } else if (totalLayers === 2) {
      summaryBadge.innerText = `检测到 2 层立体标高通道 (地面 Y=0 vs 高架 Y=+2)`;
    } else {
      summaryBadge.innerText = `检测到 ${totalLayers} 层立体楼宇/阶梯通道 (共 ${totalLayers} 层)`;
    }
  }"""

# Let's see how summaryBadge is written currently
import re
match = re.search(r'const summaryBadge = document\.getElementById\(\'layerSummaryBadge\'\);[\s\S]*?const floorSelector', code)
if match:
    new_badge_block = """const summaryBadge = document.getElementById('layerSummaryBadge');
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
  const floorSelector"""
    code = code[:match.start()] + new_badge_block + code[match.end()-len('const floorSelector'):]

# Now let's update floorTitle
match_title = re.search(r'let floorTitle = `[\s\S]*?let sliceHtml = `', code)
if match_title:
    new_title_block = """const stair = data.stair_runtime || {};
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

    let sliceHtml = `"""
    code = code[:match_title.start()] + new_title_block + code[match_title.end()-len('let sliceHtml = `'):]

with open('frontend/v2.js', 'w', encoding='utf-8') as f:
    f.write(code)

print("Updated frontend/v2.js successfully!")
