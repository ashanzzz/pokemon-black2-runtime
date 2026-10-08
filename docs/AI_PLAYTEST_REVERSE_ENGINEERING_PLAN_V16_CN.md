# AI 边玩边验收与逆向闭环计划（V16）

> 本文档已由 [V17.1 总计划](./AI_PLAYTEST_REVERSE_ENGINEERING_PLAN_V17_CN.md) supersede；V17.1
> 增加了用户批准门禁、未知状态的内存/截图决策树、战斗动画提前读结果、剧情推进状态、
> 存档/SRAM 和上下文专注文件设计。

> 目标：让另一个 AI 可以通过 HTTP API 操作真实的 BizHawk/《宝可梦黑2》实例，
> 一边玩、一边记录截图与内存证据，一边把问题反馈给维护者；维护者根据可复现的证据
> 修改代码，再由 AI 重跑同一场景验收。

## 0. 先记住当前边界

- ROM = 静态世界数据库；RAM/截图 = 当前运行时证据。静态 Zone、NPC、草地、Warp 只能
  作为候选，不能直接宣称“当前可见”或“必然触发战斗”。
- `gen5-field-grid-v1` 用于单 Zone 物理网格；同 Matrix 的 Zone 共用
  `gen5-matrix-grid-v1`；不同 Matrix 只能经已验证 Warp/Portal，不能硬拼直线。
- `/api/v1/battle/*` 当前是只读观察层。没有同时满足“菜单/光标/目标已解码 + 输入后状态闭环”时，
  战斗写接口必须保持 409，禁止盲按技能、道具、切换或逃跑。
- 普通 AI 视野是缓存投影，不等于 NDS 摄像机像素视野：
  `GET /api/v1/ai/view/current?profile=local_7x7` 返回 49 格候选；
  `GET /api/v1/ai/view/global` 返回全 Matrix 静态清单。截图必须通过显式 capture 取得。

## 1. 每次测试的启动、确认与收尾

### 启动（只允许项目启动器管理）

在项目根目录执行：

```powershell
.venv\Scripts\python.exe tools\black2_launcher.py start --no-browser
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/api/bizhawk/status
```

必须记录：`backend_pid`、EmuHawk PID、`session_id`、`bridge_version`、ROM hash、当前 frame。
期望 `bridge_connected=true`、`universal_dump=true`、`savestate` 能力可见。

如果代码刚改过，先：

```powershell
.venv\Scripts\python.exe tools\black2_launcher.py stop-backend
.venv\Scripts\python.exe tools\black2_launcher.py start --no-browser
```

`stop-backend` 只停止本项目 `run_runtime.py`，不关闭模拟器、不改 ROM；关闭模拟器使用
`tools\black2_launcher.py close-emulator`，且只对启动器拥有的 EmuHawk 发送 WM_CLOSE。

### 收尾

保存最后一张截图、最后一份 `/api/v1/runtime/snapshot`、事件日志与测试 JSON；确认没有
`prepared_action`、导航 task 或 memory trace 仍在运行，再停止后端。不要删除证据目录。

## 2. “一次观察”标准包（每个动作前后都做）

AI 不得只看一个 endpoint。每个动作生成一个 `step_NNNN` 目录或 NDJSON 记录，至少包含：

```text
before.json       /api/v1/agent/state
current.json      /api/v1/game/current
runtime.json      /api/v1/runtime/snapshot
environment.json  /api/v1/game/environment
hazards.json      /api/v1/navigation/hazards
view_local.json   /api/v1/ai/view/current?profile=local_7x7
battle.json       /api/v1/battle/state
capture.png       POST /api/dev/capture {"label":"step_NNNN_before"}
action.json       实际提交的输入及返回值
after_*.json      动作后同样的一组状态
```

每条记录带 `session_id`、`frame`、UTC 时间、动作、预期变化和实际变化。截图 URL 立即下载到
证据目录；不得把截图与另一帧的 RAM 结果合并成“同帧事实”。若 capture 没有 frame 对齐信息，
标为 `not_frame_verified`。

推荐使用：

```powershell
.venv\Scripts\python.exe tools\ai_playtest.py --once --include-global
.venv\Scripts\python.exe tools\ai_playtest.py --duration 300 --log runtime\ai_playtest.ndjson
```

playtest 默认只读轮询，遇到 battle/dialogue/transition 自动暂停；动作测试必须由 AI 明确
提交单个受控输入并立即复核。

## 3. 动作执行规则

### 普通按键

受控手工实验可用 `POST /api/actions/press`：

```json
{"button":"Right","frames":1}
```

一次只发一个方向/按钮，`frames` 尽量为 1–4；不要发送未经观察的长序列。每次返回后等待
至少一个 runtime sample，再读取 PlayerRuntime，检查位置、朝向、movement phase、dialogue、
battle 和 transition。动作前若有 blocking dialogue/menu/battle，先处理对应层。

### 语义准备动作

`POST /api/v1/agent/actions` 只接受带 `session_id`、`primary_mode` 等前置条件的语义命令，
拒绝 `raw/button_sequence`。它是异步、可过期、可取消、带 InputLease 的安全队列；执行失败或
session/frame/request 改变时必须报告 `ACTION_STALE`，而不是重试旧命令。

### 导航

1. 先读 `/api/v1/navigation/context` 与 `/api/v1/navigation/hazards`。
2. 目标用 `matrix_id + x + y + z`；点击 3D 多边形先走 `/api/v1/navigation/snap` 或
   `/api/v1/navigation/global/snap`，记录 picked object、原始点与吸附点。
3. 规划时显式指定 `movement_mode=walk|run|bike|surf`；不要把 `auto` 当作已经验证的交通工具。
4. 每一步由执行器重新检查 PlayerRuntime。发生 Zone/Warp、dialogue、battle、故事门、
   不可通行或 transport 不允许时立即暂停并输出 composite task 状态。

## 4. 导航专项验收矩阵

每一项至少做“可走方向”和“反方向”两次，保存起点/终点、矩阵、Zone、截图和逐步 frame：

| 场景 | 预期 | 失败时反馈 |
|---|---|---|
| 普通地面 walk/run | 每步 GPos 连续，run 速度/步态只作 candidate，不能穿墙 | 最后合法格、阻挡 tile、截图 |
| Bike | 允许区域可移动；草地、雪、catwalk 限制要出现在 environment/hazards | `movement_allowed` 与实际位置 |
| 水面/水边 | 非 Surf 拒绝；Surf 可走且碰撞不穿越岸线 | tile class/flags、transport mode |
| 单向 barrier/ledge | 允许方向成功，反向拒绝 | edge direction、ROM source、实际 frame |
| 草地/深草/遇敌区 | 路径严格在 region tile set，进入后可观察 encounter 事件 | region id、entry/interior、battle event |
| 同 Matrix 跨 Zone | 使用统一 Matrix 坐标，Zone 变化是 metadata，路径连续 | transition 前后 GPos/Zone |
| 跨 Matrix/室内门 | 只有 verified Warp/Portal 才允许分段任务 | portal id、两侧坐标、等待帧 |
| NPC/训练家视线 | hazards 只标 `trainer_unverified` candidate；实际 battle 事件后才能升级 | NPC id、sight_raw、遮挡状态、battle 证据 |

验收标准不是“API 返回了路径”，而是“逐格输入后 PlayerRuntime 落点与截图一致，遇到
阻挡/战斗/切图能安全停下且可恢复”。

## 5. NPC 触发战斗与野外遇敌

- 规划前读取 hazards；动态 NPC 占用、script trigger、Warp/story gate、NPC sight 都要带
  `knowledge_state`、`source`、`confidence`。`policy_source=default_only` 不是最终路线策略，
  AI 必须按任务意图重新计算（避开/主动触发/允许）。
- NPC 视线目前没有墙体遮挡验证，也没有证明普通居民是训练家。只有出现同帧截图、
  PlayerRuntime 停止/对话、随后 battle presence 的连续证据，才建立 `trainer_profile`。
- 草地遇敌测试要在保存点前后进行：进入 region、走 1/4/8 步，记录 encounter/battle 状态、
  frame 间隔和截图；未遇敌不等于“没有遇敌机制”。

## 6. 战斗验收路线（野外、训练家、天气/季节）

### 进入战斗

当 `/api/v1/agent/state` 的 attention 出现 battle，AI 立刻暂停导航，保存：

```text
/api/v1/battle/evidence
/api/v1/battle/state
/api/v1/battle/request
/api/v1/battle/field
/api/v1/battle/party
/api/v1/battle/moves
/api/v1/battle/items
/api/v1/battle/events?since=<last_cursor>
capture.png
```

同时记录进入原因：草地 encounter、NPC sight、脚本或未知。野外/训练家、单/双/三/旋转、
当前 BattleMon、敌方队伍、菜单 cursor、合法目标必须由多帧 RAM+截图配对确认，不能从
ZoneHeader 或静态 trainer 记录猜测。

### 战斗动作（当前阶段）

`/api/v1/battle/actions` 和 `/api/v1/battle/decisions` 当前应返回 409、`executed=false`。
AI 只能验证 schema、拒绝原因、request freshness 与事件记录；不能盲按 A/方向选择技能。
未来要开放“一键技能/一键给某只宝可梦道具”，必须先完成：

1. 同一 battle request 下抓到菜单出现、cursor、可用 move/item/target；
2. 提交一个最小语义命令并由 InputLease 串行化；
3. 记录输入前后同帧/近帧截图和 RAM；
4. 验证 PP、HP/status、message、turn/phase、经验/结算变化；
5. 失败、逃跑、切换、捕获都验证负路径后，才能把 `execution.available` 改为 true。

### 天气、时间、季节、战斗场地

`/api/v1/game/environment` 将交通、TileClass/Flags、Props 原始日段/季节和 ZoneHeader
静态天气分层返回；`/api/v1/battle/field` 的 battle weather/field effects 仍是 unresolved。
必须分别做晴/雨/沙/雪、昼夜、四季和具有自带天气特性的宝可梦样本，保存修改前后 frame、
截图和 RAM diff；静态 `ZoneHeader.weather` 不得替代当前战斗天气。

## 7. Savestate、重启与本机存储验收

### 状态机

1. `GET /api/dev/savestate/status`：记录 bridge 版本、popup、可用性。
2. 在安全场景 `POST /api/dev/savestate/save?slot=1`，要求返回
   `saved=true && confirmed=true`，随后截图和 `/api/v1/runtime/snapshot`。
3. 移动若干步，再 `POST /api/dev/savestate/load?slot=1`；只有
   `loaded=true && confirmed=true` 才恢复任务。复核 session/frame、Zone/GPos、截图是否回到保存点。
4. 故意用空槽或不兼容槽验证 409 分类；确认当前游戏不被破坏、已知 mismatch popup 被识别并仅关闭该弹窗。
5. 后端重启测试：保存 state → `stop-backend` → `start`，确认 EmuHawk 不退出、Bridge 重连、
   旧导航/动作因 session changed 变 stale；再读取同一槽位。

### 本机存储/ROM 文件

- `GET /api/v1/runtime/control/status` 提供项目根、ROM configured/available、PID 和生命周期范围；
  `GET /api/bizhawk/status` 提供 ROM 名称/hash、内存域和 bridge capability。
- `GET /api/dev/dumps` 只列出已有证据；`POST /api/dev/dump_full_ram` 是明确的只读全量 RAM 快照，
  仅在需要逆向时使用，并记录磁盘路径、大小、hash、截图关联。不要把真实存档文件复制到第三方。
- 清理 dump 属于删除操作，必须由用户明确确认后调用 `/api/dev/dumps/clear`。

## 8. 反馈格式：让维护者能直接修复

AI 每发现问题，必须提交一条结构化报告（不要只说“走不过去”）：

```json
{
  "case_id": "nav-bike-zone444-001",
  "objective": "Zone 446 -> 444，同 Matrix，bike",
  "session_id": "...",
  "frames": {"before": 123, "failure": 137, "after": 138},
  "api": {"endpoint": "...", "status": 409, "body": {}},
  "coordinates": {"matrix": 0, "zone": 446, "gpos": {"x": 141, "y": 2, "z": 659}},
  "screenshot": "runtime/evidence/case_id/failure.png",
  "memory": {"endpoint": "/api/dev/memory_batch_snapshot", "ranges": [], "hash": "..."},
  "expected": "...",
  "observed": "...",
  "repro_steps": ["..."],
  "safety": {"writes_performed": false, "savestate_slot": 1}
}
```

维护者修改后，AI 必须复跑同一 `case_id`，并给出 pass/fail、差异和是否引入回归。

## 9. 自动化门禁（提交代码前）

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall backend tools
.venv\Scripts\python.exe tools\ai_playtest.py --once --include-global
```

当前基线为 `394 passed, 6 skipped`（加上本计划对应的改动后以实际输出为准）。任何 API 500、
未标注的 `candidate` 升级、跨 Matrix 无 Portal 直连、输入后无 post-action 验证，都视为失败。

## 10. 维护者与测试 AI 的职责边界

- 测试 AI：启动/停止本项目、操作受控输入、保存截图/RAM/API、复现并报告；不修改 ROM，不把
  未验证事实写成确定结论。
- 维护者：审查报告、读取源代码与内存证据、修改 API/解码器/导航、补单元测试和负路径测试，
  重启后端并要求测试 AI 重跑原场景。
- 只有经过多帧、截图、RAM、动作结果四者闭环的能力，才允许从 `candidate/partial` 升级为
  `verified`，再考虑开放“一键技能/道具/自动战斗”。
