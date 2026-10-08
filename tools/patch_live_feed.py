with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

# Add appendNavTaskLog function
nav_log_helper = """
function appendNavTaskLog(msg, color) {
  const box = document.getElementById('navLiveTaskLog');
  if (!box) return;
  const time = new Date().toTimeString().split(' ')[0];
  const colorStyle = color ? `style="color:${color}; font-weight:700;"` : '';
  box.innerHTML += `<div ${colorStyle}>[${time}] ${msg}</div>`;
  box.scrollTop = box.scrollHeight;
}
"""

if "function appendNavTaskLog" not in text:
    text = nav_log_helper + "\n" + text

# Update handleExecuteNav and trackNavTask
old_exec_section = """    const res = await fetch('/api/v1/navigation/tasks', {
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
      appendLog(`[任务派发成功] 任务 ID: ${activeNavTaskId}, 状态: ${d.status}`);
      if (!isLogDrawerOpen) {
        toggleGlobalLogDrawer();
      }
      const btnCancel = document.getElementById('btnCancelNav');
      if (btnCancel) btnCancel.style.display = 'inline-flex';
      trackNavTask(activeNavTaskId);
    } else {
      const errMsg = d.error?.message || d.detail?.message || d.detail || JSON.stringify(d);
      appendLog(`[下发拒绝] ${d.error?.code || '错误'}: ${errMsg}`);
      if (!isLogDrawerOpen) {
        toggleGlobalLogDrawer();
      }
    }"""

new_exec_section = """    const box = document.getElementById('navLiveTaskLog');
    if (box) box.innerHTML = ''; // 清空上一轮监视日志
    appendNavTaskLog(`🚀 开始派发任务 POST /api/v1/navigation/tasks ➔ 目标 (${tx}, ${tz}, Y=${ty})...`);
    const statusBadge = document.getElementById('navLiveTaskStatusBadge');
    if (statusBadge) {
      statusBadge.className = 'ds-badge ds-badge-cyan';
      statusBadge.innerText = '派发中...';
    }

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
      appendLog(`[任务派发成功] 任务 ID: ${activeNavTaskId}, 状态: ${d.status}`);
      appendNavTaskLog(`✅ 任务创建成功! ID: ${activeNavTaskId} | 规划步数: ${d.progress?.total_steps || '-'} 步`, 'var(--accent-cyan)');
      if (!isLogDrawerOpen) {
        toggleGlobalLogDrawer();
      }
      const btnCancel = document.getElementById('btnCancelNav');
      if (btnCancel) btnCancel.style.display = 'inline-flex';
      trackNavTask(activeNavTaskId);
    } else {
      const errMsg = d.error?.message || d.detail?.message || d.detail || JSON.stringify(d);
      appendLog(`[下发拒绝] ${d.error?.code || '错误'}: ${errMsg}`);
      appendNavTaskLog(`❌ 下发拒绝: [${d.error?.code || 'ERROR'}] ${errMsg}`, 'var(--accent-red)');
      if (statusBadge) {
        statusBadge.className = 'ds-badge ds-badge-red';
        statusBadge.innerText = '下发被拒';
      }
      if (!isLogDrawerOpen) {
        toggleGlobalLogDrawer();
      }
    }"""
text = text.replace(old_exec_section, new_exec_section)

# Update trackNavTask
old_track_block = """// 查询追踪寻路任务状态
function trackNavTask(taskId) {
  const pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/v1/navigation/tasks/${taskId}`);
      if (!res.ok) {
        clearInterval(pollInterval);
        return;
      }
      const data = await res.json();
      const status = data.status;
      if (status === 'completed' || status === 'succeeded') {
        appendLog(`[任务圆满达成] 角色已成功落地并抵达目标坐标！`);
        clearInterval(pollInterval);
        const btnCancel = document.getElementById('btnCancelNav');
        if (btnCancel) btnCancel.style.display = 'none';
        pollPlayerRuntime();
        pollRadar();
      } else if (status === 'failed' || status === 'cancelled') {
        const errMsg = data.stop_reason?.message || data.error?.message || data.reason || '无';
        appendLog(`[任务终止] 状态: ${status}, 原因: ${errMsg}`);
        clearInterval(pollInterval);
        const btnCancel = document.getElementById('btnCancelNav');
        if (btnCancel) btnCancel.style.display = 'none';
        pollPlayerRuntime();
        pollRadar();
      }
    } catch (e) {
      clearInterval(pollInterval);
    }
  }, 1000);
}"""

new_track_block = """// 查询追踪寻路任务状态 (双重投射到常驻抽屉与面板专属监视流)
function trackNavTask(taskId) {
  const pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/v1/navigation/tasks/${taskId}`);
      if (!res.ok) {
        clearInterval(pollInterval);
        return;
      }
      const data = await res.json();
      const status = data.status;
      const prog = data.progress || {};
      const completed = prog.completed_steps || 0;
      const total = prog.total_steps || 0;
      const cur = data.current?.position || {};
      const curStr = (cur.x !== undefined) ? `(${cur.x}, ${cur.z}, Y=${cur.y})` : '';

      const statusBadge = document.getElementById('navLiveTaskStatusBadge');
      if (statusBadge) {
        if (status === 'executing') {
          statusBadge.className = 'ds-badge ds-badge-cyan';
          statusBadge.innerText = `执行中 (${completed}/${total}步)`;
        } else if (status === 'completed' || status === 'succeeded') {
          statusBadge.className = 'ds-badge ds-badge-green';
          statusBadge.innerText = '已圆满达成';
        } else if (status === 'failed' || status === 'cancelled') {
          statusBadge.className = 'ds-badge ds-badge-red';
          statusBadge.innerText = `任务${status === 'failed' ? '失败' : '取消'}`;
        }
      }

      if (status === 'executing') {
        appendNavTaskLog(`⏳ 推进中: 已完成 ${completed}/${total} 步 | 当前坐标: ${curStr} | 物理帧: ${data.current?.frame || '-'}`);
      } else if (status === 'completed' || status === 'succeeded') {
        const arrival = data.arrival?.position || cur;
        appendLog(`[任务圆满达成] 角色已成功抵达目标坐标 (${arrival.x}, ${arrival.z}, Y=${arrival.y})！`);
        appendNavTaskLog(`🎉 任务圆满达成！角色已准确落地于 (${arrival.x}, ${arrival.z}, Y=${arrival.y})！总耗时: ${total} 步已全量落地。`, 'var(--accent-green)');
        clearInterval(pollInterval);
        const btnCancel = document.getElementById('btnCancelNav');
        if (btnCancel) btnCancel.style.display = 'none';
        pollPlayerRuntime();
        pollRadar();
      } else if (status === 'failed' || status === 'cancelled') {
        const stopReason = data.stop_reason || {};
        const errMsg = stopReason.message || data.error?.message || data.reason || '原因未知';
        appendLog(`[任务终止] 状态: ${status}, 原因: ${errMsg}`);
        appendNavTaskLog(`❌ 任务终止: [${stopReason.code || status}] ${errMsg}`, 'var(--accent-red)');
        clearInterval(pollInterval);
        const btnCancel = document.getElementById('btnCancelNav');
        if (btnCancel) btnCancel.style.display = 'none';
        pollPlayerRuntime();
        pollRadar();
      }
    } catch (e) {
      clearInterval(pollInterval);
    }
  }, 1000);
}"""
text = text.replace(old_track_block, new_track_block)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js updated with live task monitor feed and detailed logging!")
