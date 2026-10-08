# AI 导航与战斗验收协议（v15）

## 目标

给外部 AI 一个稳定、可追溯、不会误操作的黑 2/白 2 观察面：

* `gen5-field-grid-v1` 是 Zone 内的网格坐标；
* `gen5-matrix-grid-v1` 是同 Matrix 地表拼接后的统一坐标；
* Zone 只表示 ROM 所有权，不改变同 Matrix 的空间身份；
* 跨 Matrix 只能通过已验证 Warp/Portal 状态机，不能因为静态邻近就直连。

## 读取顺序

1. `/api/v1/agent/state`：确认 session、主模式和是否有 battle/dialogue/transition 注意事项。
2. `/api/v1/game/current`：读取分层状态；battle 优先级高于探索。
3. `/api/v1/game/environment`：读取交通模式、当前 TileClass、草地/水面、Props 的原始日段/季节，以及 ZoneHeader 的静态天气/战斗背景候选。
4. `/api/v1/navigation/hazards`：读取动态占用、NPC 视线候选、脚本触发、Warp、故事门及默认避让策略；同时给出玩家周围的单向 barrier/ledge 与草地/水面/自行车限制。`policy_source=default_only`，真正计划必须重新计算 policy。
5. `/api/v1/ai/view/current?profile=local_7x7`：取得 49 格的统一坐标局部投影。
6. `/api/v1/ai/view/global`：取得 Matrix 全局清单；需要逐格地形时再调用 `/api/v1/ai/map/tile`。
7. 只有在没有注意事项时才调用 `/api/v1/navigation/plans`；计划仍是只读。
8. `/api/v1/navigation/tasks` 执行时逐格核对 PlayerRuntime。发生 battle、dialogue、切图、落点不一致或桥断开即停止。

## 视野语义

`local_7x7` 是稳定的 49 格 ROM/运行时投影，并不声称等同 NDS 摄像机实际可见矩形；当前 NDS camera、遮挡和上/下屏可见性仍未验证。`global_static` 是 ROM Matrix 清单，不是“AI 已经看过”的记忆。

如果要给 AI 记忆，外部客户端应保存每次 NDJSON 样本，按 `session_id + frame + spatial_key` 合并；不要把未采样的全局格子当成已观察事实。

## 战斗安全门

当前可以使用的战斗接口是只读 evidence：BusyFlag、持久队伍指针/容量、checksum 通过的 party/move 槽。`battle_kind`、phase、BattleMon、敌方队伍、招式/道具合法性、天气、场地效果、时间季节均必须显示为 `unresolved` 或 candidate。

在取得成对的野外/训练家样本，并完成“菜单出现 → 选择 → 目标 → post-action RAM/画面结果”的逐帧闭环以前，禁止一键技能、切换、道具、捕获或逃跑写入。

## 离线 AI 观察脚本

```powershell
.venv\Scripts\python.exe tools\ai_playtest.py --once --include-global
.venv\Scripts\python.exe tools\ai_playtest.py --duration 300 --log runtime\ai_playtest.ndjson
```

脚本只轮询 GET 接口并在 battle/dialogue/transition 时暂停；NDJSON 可交给 Luna/Terra 做问题归因。BizHawk 未连接时，输出会保留 `bridge_connected=false` 和 unresolved，而不会伪造位置或战斗结果。

## 实机验收矩阵

连接 BizHawk 后按顺序记录：步行、跑步、自行车、Surf；单向 barrier/ledge 双向尝试；草地遇敌；训练家视线；Warp 同 Matrix 与跨 Matrix；战斗命令菜单、招式、目标、伤害/结算、经验。每项都要有 frame、截图、RAM evidence 和 post-action 落点/状态，缺一项只能保持 candidate。
