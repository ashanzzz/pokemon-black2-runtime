with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

old_exec_nav = """    if (res.ok && (d.task_id || d.id)) {
      activeNavTaskId = d.task_id || d.id;
      appendLog(`[任务已下发] 任务 ID: ${activeNavTaskId}, 状态: ${d.status}`);
      const btnCancel = document.getElementById('btnCancelNav');
      if (btnCancel) btnCancel.style.display = 'inline-flex';
      trackNavTask(activeNavTaskId);
    } else {
      appendLog(`[下发拒绝] ${d.error?.code || '错误'}: ${d.error?.message || JSON.stringify(d)}`);
    }"""

new_exec_nav = """    if (res.ok && (d.task_id || d.id)) {
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
text = text.replace(old_exec_nav, new_exec_nav)

old_track_nav = """      if (status === 'completed') {
        appendLog(`[任务达成] 角色已成功抵达目标坐标！`);
        clearInterval(pollInterval);
        const btnCancel = document.getElementById('btnCancelNav');
        if (btnCancel) btnCancel.style.display = 'none';
        pollPlayerRuntime();
        pollRadar();
      } else if (status === 'failed' || status === 'cancelled') {
        appendLog(`[任务终止] 状态: ${status}, 原因: ${data.error?.message || data.reason || '无'}`);
        clearInterval(pollInterval);
        const btnCancel = document.getElementById('btnCancelNav');
        if (btnCancel) btnCancel.style.display = 'none';
        pollPlayerRuntime();
        pollRadar();
      }"""

new_track_nav = """      if (status === 'completed' || status === 'succeeded') {
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
      }"""
text = text.replace(old_track_nav, new_track_nav)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("v2.js updated: auto-open log drawer on dispatch, handle 'succeeded' and stop_reason.message cleanly!")
