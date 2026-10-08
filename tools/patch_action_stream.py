with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

old_action_mapping = """      actionListElem.innerHTML = actions.map((a, idx) => {
        const dirZh = { 'North': '向北', 'South': '向南', 'East': '向东', 'West': '向西' }[a.direction] || a.direction;
        const dur = a.steps * modeFpt;
        return `${idx + 1}. [${dirZh}直行 ${a.steps} 格] 从 (${a.from?.x}, ${a.from?.y}, ${a.from?.z}) 直通 (${a.to?.x}, ${a.to?.y}, ${a.to?.z}) · 持续按键 ${dur} 帧 · 实时 RAM 制动`;
      }).join('<br>');"""

new_action_mapping = """      actionListElem.innerHTML = actions.map((a, idx) => {
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
      }).join('');"""

text = text.replace(old_action_mapping, new_action_mapping)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js action stream enriched with gait mode (run, bike, walk, surf) and elevation badges!")
