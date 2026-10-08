import re

with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read().replace("\r\n", "\n")

new_clean_block = """async function handlePartyTeachMove() {
  const pSlot = parseInt(document.getElementById('teachPartySlot')?.value || '1', 10);
  const mSlot = parseInt(document.getElementById('teachMoveSlot')?.value || '1', 10);
  const mId = parseInt(document.getElementById('teachMoveId')?.value || '82', 10);
  appendLog('[学习技能] 正在为队伍席位 ' + pSlot + ' 槽位 ' + mSlot + ' 学习技能 #' + mId + ' (POST /api/v1/game/party/teach-move)...');
  try {
    const res = await fetch('/api/v1/game/party/teach-move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ party_slot: pSlot, move_slot: mSlot, move_id: mId })
    });
    const d = await res.json();
    if (res.ok) {
      const pName = d.pokemon ? '【' + d.pokemon.species_name + ' Lv.' + d.pokemon.level + '】' : '';
      const oldName = d.old_move ? '「' + d.old_move.name + '」' : '';
      const newName = d.new_move ? '「' + d.new_move.name + '」' : '「' + (d.move_name || d.new_move_id) + '」';
      appendLog('[学习成功] 席位 ' + pSlot + pName + ' 招式槽位 ' + mSlot + ' 已学会 ' + newName + '（替换原招式 ' + oldName + '），物理RAM校验码: ' + d.checksum);
      if (d.current_moves && d.current_moves.length) {
        appendLog('[当前技能配置] ' + d.current_moves.join(' · '));
      }
      pollParty();
    } else {
      appendLog('[学习失败] ' + (d.detail || JSON.stringify(d)));
    }
  } catch (e) {
    appendLog('[学习异常] ' + e);
  }
}

async function handlePartySwapOrder() {
  const slotA = parseInt(document.getElementById('partySwapSlotA')?.value || 1, 10);
  const slotB = parseInt(document.getElementById('partySwapSlotB')?.value || 2, 10);
  appendLog("[队伍调序] 正在交换队伍席位 " + slotA + " 与 " + slotB + " (POST /api/v1/game/party/swap)...");
  try {
    const res = await fetch('/api/v1/game/party/swap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slot_a: slotA, slot_b: slotB })
    });
    const d = await res.json();
    if (res.ok) {
      if (d.swapped_a && d.swapped_b) {
        appendLog('[调序成功] 席位 ' + d.slot_a + '【' + d.swapped_a.species_name + ' Lv.' + d.swapped_a.level + '】↔ 席位 ' + d.slot_b + '【' + d.swapped_b.species_name + ' Lv.' + d.swapped_b.level + '】已成功互换！');
      } else {
        appendLog('[调序成功] 席位 ' + slotA + ' 与 ' + slotB + ' 顺序已调换，首发已更新！');
      }
      if (d.latest_lineup && d.latest_lineup.length) {
        appendLog('[最新出战阵型] ' + d.latest_lineup.join(' · '));
      }
      if (d.lead_pokemon) {
        appendLog('[首发领队更新] 席位 1【' + d.lead_pokemon.species_name + ' Lv.' + d.lead_pokemon.level + '】(出战首发)');
      }
      pollParty();
    } else {
      appendLog("[调序失败] " + (d.detail || JSON.stringify(d)));
    }
  } catch (e) {
    appendLog("[调序异常] " + e);
  }
}

async function handleStepForward() {
  appendLog('[指令下发] 正在调用主线自驱动步进接口 POST /api/v1/agent/story/step...');
  try {
    const res = await fetch('/api/v1/agent/story/step', { method: 'POST' });
    const data = await res.json();
    appendLog('[执行成功] 主线步进结果: ' + JSON.stringify(data));
    pollPlayerRuntime();
  } catch (e) {
    appendLog('[执行失败] 主线步进发生异常: ' + e);
  }
}

async function handleNurseRecovery() {
  appendLog('[指令下发] 调用 POST /api/v1/agent/automation/recovery 触发宝可梦中心全恢复...');
  try {
    const res = await fetch('/api/v1/agent/automation/recovery', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ service: 'nearest', movement_mode: 'auto' })
    });
    const d = await res.json();
    if (res.ok) {
      appendLog('[医护任务启动] 任务 ID: ' + (d.task_id || 'recovery_ok') + ', 状态: ' + (d.status || 'started'));
      appendNavTaskLog('[医护恢复启动] 正在调度宝可梦中心护士 (2100号脚本)...', 'var(--accent-blue)');
      if (d.task_id) {
        for (let i = 0; i < 20; i++) {
          await new Promise(r => setTimeout(r, 800));
          const tRes = await fetch('/api/v1/agent/automation/tasks/' + d.task_id);
          if (tRes.ok) {
            const td = await tRes.json();
            if (td.status === 'succeeded') {
              appendLog('[医护恢复完成] 宝可梦中心护士已完成全员状态与体力回满！');
              appendNavTaskLog('[医护恢复完成] 全队血量与状态已回满 (2100号脚本)', 'var(--accent-green)');
              break;
            } else if (td.status === 'failed') {
              appendLog('[医护恢复未完成] ' + JSON.stringify(td.stop_reason || td.status));
              break;
            }
          }
        }
      }
      pollParty();
    } else {
      appendLog('[恢复响应] ' + JSON.stringify(d));
    }
  } catch (e) {
    appendLog('[恢复异常] ' + e);
    appendNavTaskLog('[恢复异常] ' + e, 'var(--accent-red)');
  }
}"""

# Replace from async function handlePartyTeachMove() to async function handleMoveAction(slot)
pattern = re.compile(r"async function handlePartyTeachMove\(\).*?async function handleMoveAction\(slot\)", re.DOTALL)
match = pattern.search(text)
assert match is not None
text = text[:match.start()] + new_clean_block + "\n\nasync function handleMoveAction(slot)" + text[match.end():]

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)
print("Replaced handlePartyTeachMove .. handleNurseRecovery cleanly!")