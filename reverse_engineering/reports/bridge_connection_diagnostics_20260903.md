# TEST REPORT — BizHawk bridge 连接状态与日志诊断

## Goal

解释网页显示 `BizHawk bridge 未连接` 的原因，并让手动对话检查点能可靠地取得 bridge 帧号与原始 RAM。

## Method

- 读取当前本地 API `/api/bizhawk/status`；
- 检查 Windows TCP 监听/Established 状态及进程；
- 检查 `runtime.log`、Lua bridge 的连接/heartbeat 实现；
- 不修改游戏 RAM，不发送按键。

## Raw observations

在 2026-09-03 当前运行实例中：

- API 进程 PID 5880 监听 `127.0.0.1:8765`、`:8766`、`:8768`；BizHawk PID 21120 从临时端口连接到 `127.0.0.1:8766`，TCP 状态为 `Established`。
- 旧 API `/api/bizhawk/status` 返回 `connected=false`，但 `frame=3459780` 持续增长，`last_heartbeat` 在当前时间附近，hello 中含 `read_batch=true`、NDS、ROM hash `8DB71663502BBF3B43AC3C9052EC390C390BE62F`。
- `runtime.log` 曾记录 `SocketTransport ... BizHawk Bridge connected!` 与 `Hello received!`。这证明 Lua Console 中的 bridge 脚本至少成功建立过连接；仅打开 Lua Console 不等于脚本正在运行，但当前 TCP 连接证明脚本仍在发送数据。

## Hypothesis

**Probable：旧 `SocketTransport._handle_client` 的断开竞态。** 旧连接的 handler 在 `finally` 中无条件把全局 `writer`、`reader` 和 `_connected` 清空；若重连已经把全局 writer 换成新连接，旧 handler 仍会把新连接报告为 disconnected。`last_heartbeat` 和 `last_frame` 继续更新，因此形成“frame 在动、connected=false”的矛盾状态。

## Changes

- `socket_transport.py`：断开时只允许拥有当前 active writer 的 handler 清除连接状态；增加 `RotatingFileHandler`，写入 `logs/bridge_transport.log`；记录 listen、accept、hello、heartbeat、重连竞态、请求超时和连接状态变化。
- `app.py`：新增 `/api/dev/bridge_log`，并在 `/api/bizhawk/status` 返回 `transport` 诊断字段（flag、writer、heartbeat age、pending requests）。
- 手动检查点接口继续使用同一次 `memory.read_batch` 返回的 bridge `frame`，不使用 heartbeat 估算帧号。

## Verification

- Python `py_compile` 通过；新路由在新进程导入后的 OpenAPI 路由表中存在。
- 初次检查时 PID 5880 是未重启的旧进程，因此实时 `/api/dev/bridge_log` 与手动检查点路由返回 404；这不是 Lua Console 的新故障。

## Follow-up verification after restart

- 已按用户请求停止旧 API 并运行 `run_runtime.py`；新 Uvicorn PID 为 20240，BizHawk PID 21120 未关闭。
- `/api/bizhawk/status` 现在返回 `connected=true`、`transport.connected_flag=true`、`writer_present=true`，heartbeat age 小于 1 秒，bridge version `1.2.0-unified`。
- `logs/bridge_transport.log` 已记录新的 listen、`request_sent/request_completed` 和 heartbeat；新 `/api/dev/bridge_log` 与 `/api/dev/dialogue_checkpoint` 均可访问。

## Next exact test

在 API 所在终端按 `Ctrl+C`，从项目根目录重新运行 `.\.venv\Scripts\python.exe run_runtime.py`。Lua bridge 通常会自动重连；若页面仍未连接，再在 Lua Console 重新加载 `bridge/bizhawk/black2_bridge.lua`。确认网页显示 `BizHawk bridge 已连接` 后，按 EXP_012 页面顺序采集滚屏重叠、第三页（若出现）和对话结束状态。

## Unresolved

真实 TextPrinter/Window 字段、当前可视行和说话 NPC Actor 仍未验证；本报告只解决传输状态可观测性与证据采集前置条件。
