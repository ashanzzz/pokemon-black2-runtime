# AI 游戏测试、内存逆向与上下文管理总计划（V17.1）

> 本次修订补充“游戏推进所需事实”和“可持续内存逆向基础设施”。本文仍是当前权威计划。

## 重要门禁：本文件批准前不执行任何测试

当前阶段只做设计和代码审查。没有用户明确回复“批准测试”之前，AI 不得：

- 启动/重启后端或 BizHawk；
- 发送按键、触摸、导航 task、战斗命令；
- 读取新的实时 RAM、截图、savestate 或本机存档；
- 把静态 ROM 推断升级为运行时事实。

批准后也必须按本计划的小步、可恢复流程执行。停止测试只停止本项目后端，不默认关闭用户的
EmuHawk；关闭模拟器、加载/覆盖存档属于单独的明确动作。

---

## 1. 目标和最终交付

我们要得到一个“可被 AI 使用、可被人审核、可被证据复现”的黑2运行时：

1. 自动寻路支持 walk/run/bike/surf、草地/深草/水面、单向 barrier/ledge、NPC 占位和视线候选、
   同 Matrix 跨 Zone，以及经过已验证 Warp 的跨 Matrix 分段任务。
2. 前端和 API 能清楚表示标题、继续/新游戏选择、场景、对话、菜单、战斗菜单箭头、动作阶段、
   结算和切图；未知就显示 `unresolved`，不显示一个看似确定的假值。
3. AI 能在不知道下一步时按“截图 → 同帧/近帧 RAM → 特征 diff → 小步动作 → 再验证”的顺序
   自主诊断，并保存可供维护者复跑的 evidence bundle。
4. 战斗先做到可靠只读，再逐项开放“选招式、选目标、切换、道具、投球、逃跑”。任何写操作都必须
   有当前 request、菜单 cursor、合法目标和 post-action 闭环。
5. 上下文分为长期记忆、短期测试记忆、当前状态和场景专注包；AI 只加载当前任务需要的内容，
   不把整个游戏日志塞进提示上下文。

## 2. 证据等级和未知处理原则

每个事实带四个字段：`status`、`confidence`、`source`、`frame/session_id`。

- `verified`：同一 session 的截图、RAM、动作结果三者一致，并在至少两个样本中复现。
- `probable`：结构链/校验正确，但还缺少可见像素或语义配对。
- `candidate`：静态 ROM、启发式特征或单个样本，只能用于提示/规划候选。
- `unresolved`：尚未解码；不能作为输入授权。

当 AI “不知道做什么”或“状态说不清”时，固定执行以下决策树：

```text
停止输入
  ├─ 读取 /api/v1/agent/state、/api/v1/game/current、/api/v1/runtime/health
  ├─ 显式 POST /api/dev/capture（取得真实 NDS PNG 和 frame）
  ├─ 用同一个 bridge frame 做 memory_batch_snapshot（只读、明确范围）
  ├─ 对比最近一次 bundle：屏幕/状态/字节哪些变化？
  ├─ 能匹配已知状态机 → 仅执行一个最小动作
  └─ 不能匹配 → 保存 unknown checkpoint，提出逆向任务，不盲按
```

full RAM、pattern scan、write trace 只能用于有明确假设的 bounded 实验；不得因为“看不懂”就
连续扫描并向 AI 宣称已经找到含义。

## 3. 统一状态模型（前端必须显示）

后端以一个非互斥 layer 状态为准，前端同时显示底层场景和覆盖层：

```json
{
  "primary_context": "title|save_select|field|dialogue|menu|battle|transition|unknown",
  "layers": {
    "exploration": {"active": true, "zone": 439, "gpos": {"x": 105, "y": 1, "z": 694}},
    "dialogue": {"active": false, "speaker": null, "choices": []},
    "menu": {"active": false, "kind": null, "cursor": null, "items": []},
    "battle": {"active": null, "phase": "unresolved", "request_id": null},
    "transition": {"active": false, "from_zone": null, "to_zone": null}
  },
  "input_owner": "exploration|dialogue|menu|battle|transition|none",
  "evidence": {"frame": 0, "screenshot": {}, "memory": {}, "status": "..."}
}
```

前端 Workbench、AI view、事件流必须引用同一 schema。至少要有以下画面状态的可见字段：

- 标题：按键提示、动画/静止、可接受 `Start/A/touch`；
- 存档选择：Continue/New/礼物等选项、光标、是否有存档、游玩时间/图鉴/徽章候选；
- 场景：Zone、Matrix、GPos/WPos、朝向、交通模式、TileClass/Flags、草地/水/碰撞；
- 对话：打印器 active、可见文本/加载文本、speaker/choices（无法解码时明确 unresolved）；
- 菜单：菜单 kind、cursor、合法项 candidate 和截图；
- 战斗：battle presence、kind/format/phase、双方 active slot、箭头/cursor、招式/道具/目标、
  message printer、动画/结算阶段；未验证字段显示 unresolved。

## 4. Evidence Bundle：一次动作的最小闭环

每次动作前后各保存一个 bundle，文件名包含 `session_id` 和 `frame`：

```text
case_id/
  manifest.json             # 目标、动作、预期、版本、ROM hash
  before.agent.json
  before.current.json
  before.runtime.json
  before.environment.json
  before.battle.json
  before.view.local.json
  before.png
  action.request.json       # 只含实际发出的一个动作
  action.response.json
  after.agent.json
  after.current.json
  after.runtime.json
  after.environment.json
  after.battle.json
  after.view.local.json
  after.png
  memory.batch.json         # 明确 range、同帧号、sha256
  diff.json                 # 字段 diff + RAM byte diff + 截图 hash
  notes.md
```

`before.png`/`after.png` 若不是同一 Lua tick，只能标 `near_frame`；不得伪造 `atomic`。所有
原始字节保留，解释另存为 `interpretation`，以后发现错了可以重算。

## 5. 读取接口和生命周期操作（批准后才可执行）

### 启动与重启

```powershell
.venv\Scripts\python.exe tools\black2_launcher.py start --no-browser
GET /health
GET /api/bizhawk/status
GET /api/v1/runtime/control/status
```

代码修改后用 `stop-backend` → `start --no-browser` 刷新 Python；这不会主动关闭 EmuHawk。
`/api/v1/runtime/restart` 只能用 localhost token，重启后旧 action/task 必须因 session 变化变 stale。

### 截图、RAM 和存档

- 截图：`POST /api/dev/capture {"label":"case_before"}`，下载 `capture_url` 并记录 frame/hash。
- 有限 RAM：`POST /api/dev/memory_batch_snapshot`，范围总长度受限；跨关键动作用同一组 ranges。
- 结构扫描：`POST /api/dev/memory_pattern_scan`；写跟踪只用明确地址和最多 3 帧的
  `POST /api/dev/memory_write_trace`，该接口本身不写 RAM。
- Savestate：`GET /api/dev/savestate/status` → `POST /api/dev/savestate/save?slot=N` →
  `POST /api/dev/savestate/load?slot=N`；只有 `confirmed=true` 才认为成功。失败要保存 popup、
  409 分类和“当前游戏保持不变”证据。
- 本机文件：`/api/v1/runtime/control/status`（ROM 是否存在、项目根、PID）和
  `/api/bizhawk/status`（ROM hash、内存域、bridge capabilities）；full dump 只在 bounded
  逆向任务中生成，禁止上传第三方。

## 6. 导航、NPC 和移动专项

规划步骤固定为：

1. `GET /api/v1/navigation/context`；
2. `GET /api/v1/navigation/hazards?radius=...`；
3. 3D 命中点先 `POST /api/v1/navigation/snap` 或 `/global/snap`；
4. `POST /api/v1/navigation/plans`，明确 `matrix_id + x + y + z` 和 `movement_mode`；
5. `POST /api/v1/navigation/tasks`，逐格复核 PlayerRuntime。

验证范围：普通 walk/run、Bike 禁行草/雪/catwalk、水面 Surf、单向 barrier/ledge 双向、同 Matrix
跨 Zone、已验证 Warp 跨 Matrix、草地 region 巡逻、NPC 动态占位和 sight candidate。
`trainer_unverified` 只有在“同帧 NPC 视线 + 停止/对话 + 后续 battle presence”成立后才能升级。
遇到 dialogue/battle/transition/故事门时任务安全暂停，不能继续发送方向键。

## 7. 战斗逆向和快速执行路线

### 7.1 先做只读战斗状态机

以 `/api/v1/battle/evidence` 为底层采样，建立：

```text
NONE → ENTERING → MESSAGE → COMMAND_MENU → TARGET_MENU
     → ANIMATION → RESULT_MESSAGE → EXP/REWARD → EXIT
```

每个状态需要截图和 RAM 特征：BusyFlag、FieldStatus、Battle request、menu cursor、printer
activity、BattleMon/party pointer、HP/status/PP、message/animation latch。先验证状态转换，
再验证字段含义；动画阶段读取到的值只能标 `post_effect_candidate`，直到结算/截图确认。

### 7.2 野外战斗专注包

进入原因若是草地/冲浪遇敌，AI 加载：当前队伍、每只宝可梦 HP/status/招式 PP、背包恢复品、
精灵球数量、对方 species/level（若已解码）、捕获需求和逃跑条件。目标是回答“要不要捕获/升级/逃跑”，
不把训练家 roster 规则混进来。

### 7.3 训练家战斗专注包

只加载当前我方 active Pokémon、可用技能/目标、对手当前可见信息和战斗规则；不因“对方队伍未解码”
而阻塞已验证的战术接口。训练家身份按证据链处理：进入战斗前由 RuntimeHub 保存同 session 的 Zone/坐标，
读取 bounded `btl_setup.c`→`strbuf.c` 的 UTF-16LE 名字，并由 `GET /api/v1/battle/zone/{zone_id}/trainer-candidates` 解析地图脚本中的 `TrainerBattle`，再回填 TRData/TRPoke
姓名、类别、队伍；最后用当前 BattlePokeParam 的种族做唯一交叉匹配。只有 runtime trainer_id、当前战斗字符串+队伍匹配，或唯一脚本候选
匹配时才显示训练家姓名 candidate；多候选、脚本旗标未验证时保持 unresolved，不从 sprite、截图或物种单独猜测。
RuntimeHub 在检测到战斗开始时自动取一份身份快照，写入 `battle_identity_history.ndjson`、短期记忆和
`battle.identity.observed` 事件；战斗结束后通过 `/api/v1/battle/identity/history` 查看，避免旧战斗 RAM
堆被复用后误报为当前遭遇。该观察器只读 RAM/ROM，不发送按键、不写模拟器内存；事件中的 `candidate` 和
`unresolved` 标签必须原样保留。

### 7.4 道馆/场地专注包

战斗前后保留道馆 Zone、地形、可互动对象、单向机关、入口/出口和 Warp；战斗接口只读取 battle
layer，不把 overworld ZoneHeader 天气冒充战斗天气。

### 7.5 “一键技能/道具/投球”开放门槛

建议 API 形状（现在只允许返回 409 contract）：

```json
POST /api/v1/battle/decisions
{
  "battle_id": "verified-id",
  "request_id": 123,
  "commands": [{"type":"throw_ball", "actor":"player:0", "item_id": 1, "target":"opponent:0"}]
}
```

真正启用前必须逐条证明：

1. `request_id` 未过期且菜单/光标与截图一致；
2. move/item/ball 在当前模式合法，目标 slot 合法；
3. InputLease 期间只发语义动作，不接受 raw button queue；
4. 动画尚未结束时可以读取 HP/status/PP 的候选变化，但动作完成前不报告最终成功；
5. post-action 检查 request/phase、HP/status/PP、消息、经验/道具数量和截图；
6. 失败、取消、目标无效、捕获失败、逃跑失败各有可复现的负测试；
7. 先灰度开放单个 `use_move`，再开放 `use_item`，最后才开放 `throw_ball/switch/run`。

## 8. 动画阶段的“提前读结果”设计

战斗 API 分开返回 `observed_now` 和 `committed_result`：

```json
{
  "phase": "ANIMATION",
  "observed_now": {
    "player_hp": {"value": 23, "status": "post_effect_candidate", "frame": 1002},
    "opponent_hp": {"value": 0, "status": "post_effect_candidate", "frame": 1002}
  },
  "committed_result": null,
  "settlement_gate": {"requires": ["result_message_done", "request_changed_or_battle_exit"]}
}
```

这样 AI 可以在动画期间准备下一步分析，但只有 `settlement_gate` 满足后才执行下一条输入。
如果 HP 字节在动画中多次变化，保存完整时间序列，禁止只取最后一次并称为最终伤害。

## 9. 上下文文件和专注力路由

建议在 `runtime/ai_context/` 保存四类小文件（均为本机、可审计 JSON/Markdown）：

```text
long_term.md       # 永久架构事实、已验证地址、API 安全规则（小而稳定）
short_term.ndjson  # 最近若干 case、错误、假设和待复现步骤（自动裁剪）
current_state.json  # 当前 session/frame/Zone/GPos/layer/savestate/action owner
focus.json          # 当前任务类型和需要加载的字段白名单
```

专注路由示例：

- 野外战斗：`focus=wild_battle`，加载 party/items/balls/opponent/capture_goal；
- 训练家战斗：`focus=trainer_battle`，加载 battle request/moves/targets/turn，不加载捕获决策；
- 道馆：`focus=gym_navigation`，加载 Zone/地形/机关/入口出口/NPC hazard；
- 普通寻路：`focus=route`，加载 current view/hazards/transport/Warp，不加载全战斗 roster。

每次 focus 切换记录原因和引用的 evidence bundle；上下文超过上限时优先丢弃旧截图描述，保留
`case_id`、结论、证据路径和未解决假设。

## 10. 测试 AI 与维护者的交接格式

测试 AI 只提交结构化报告，不直接改 ROM：

```json
{
  "case_id": "battle-wild-ball-001",
  "focus": "wild_battle",
  "objective": "验证投球前目标/道具合法性",
  "session_id": "...",
  "frames": {"before": 1000, "animation": 1002, "settled": 1030},
  "screenshots": [".../before.png", ".../animation.png", ".../settled.png"],
  "memory_bundle": ".../memory.batch.json",
  "api_calls": [{"method":"GET","path":"/api/v1/battle/state","status":200}],
  "expected": "...",
  "observed": "...",
  "hypothesis": "...",
  "repro_steps": ["..."],
  "writes_performed": false,
  "next_experiment": "..."
}
```

维护者根据报告修改解码器/API/前端，补单元测试和负路径测试；然后让测试 AI 用同一 `case_id`
复跑。只有“测试 AI 复跑通过 + 全量回归通过 + 证据等级升级”才合并能力。

## 11. 批准后的阶段门

1. **M0 连接/生命周期**：启动、截图、RAM batch、savestate save/load、后端重启，零 API 500。
2. **M1 画面状态**：标题/存档选择/场景/对话/菜单状态机和前端显示。
3. **M2 导航**：walk/run/bike/surf、草地、水、单向、NPC、同 Matrix/跨 Matrix Warp。
4. **M3 战斗只读**：野外/训练家 presence、phase、队伍/招式/道具/天气/场地候选。
5. **M4 单动作灰度**：一个已验证 move 的闭环，再逐步扩展道具、切换、投球、逃跑。
6. **M5 长时间 AI playtest**：短期记忆裁剪、异常恢复、跨 session、证据索引和回归报告。

每个阶段都可以停在 `candidate/partial/unresolved`；“不知道”是触发证据采集的信号，不是继续
盲操作的许可。

---

## 12. 推进剧情必须读取的状态（新增）

自动寻路不是只把角色送到坐标。AI 必须知道“到了以后是否能推进剧情”，因此在当前状态和
专注包中加入以下只读字段；没有验证就显示 `unresolved`：

### 12.1 故事进度和脚本

- 当前主线/支线 objective、事件编号、脚本状态、对话实例、选择结果；
- StoryFlag、EventFlag、ScriptWork、门/机关开关、已触发/未触发状态；
- 当前地图是否处于 cutscene、黑屏、淡入淡出、强制移动或等待输入；
- 触发器的前置条件和完成条件（NPC 对战、道馆机关、传送门、剧情锁）。

内存逆向时只登记“地址 + 类型 + frame + 原始 bytes + 触发前后 diff”，不根据一个 0/1 字节
直接命名为某个剧情 flag。至少要有一次保存点前后或明确剧情动作前后的配对样本。

### 12.2 进度门和能力门

路线规划需要把下列条件当成可计算的 gate：徽章数量/种类、关键道具、HM/技能许可、
自行车、Surf/Waterfall 等移动能力、NPC 已否战斗、道馆机关是否开启、昼夜/季节条件。
Gate 输出必须包含：`required`、`observed`、`status`、`source`、`blocking_reason`。未知 gate
按 hard block 处理，除非用户明确选择“实验性穿过”。

### 12.3 物品、队伍和 PC

为游戏推进和战斗准备，建立只读的：

- Party 六槽：species、level、HP/max HP、status、moves/PP、经验、持有物（加密/校验未通过则 raw）；
- Bag 分袋、数量、关键道具、TM/HM、精灵球、恢复品；
- PC 盒子容量、当前箱、槽位摘要（只在需要时 bounded 读取）；
- 钱、徽章、图鉴/捕获计数、游戏时间和游玩时长。

这些字段与 `session_id`/存档 slot 绑定；savestate load 或真实存档切换后全部重新验证，不能
沿用旧缓存。野外战斗专注包加载捕获和补给信息，训练家战斗专注包只加载战术必需信息。

## 13. 内存逆向登记和证据库（新增）

### 13.1 地址注册表

在 `runtime/ai_context/memory_registry.json` 维护每个候选字段：

```json
{
  "field_id": "battle.field_status.busy",
  "rom_profile": "IREJ-rev1",
  "domain": "Main RAM",
  "address_or_chain": ["GameData", "+0x...", "FieldStatus", "+0x..."],
  "type": "u8",
  "endianness": "little",
  "observed_frames": [100, 101, 130],
  "status": "candidate",
  "confidence": 0.75,
  "raw_samples": [0, 1, 0],
  "interpretation": "BusyFlag candidate; battle kind unresolved",
  "invalidated_by": ["session_changed", "rom_hash_changed", "pointer_invalid"]
}
```

注册表必须区分静态地址、指针链、动态分配地址、镜像/缓存地址；记录 back-reference、范围
校验、checksum、是否可能加密/乱序。任何“已解码名字”都要能回到原始 bytes 和实验 case。

### 13.2 特征实验顺序

在不知道某块内存含义时按以下顺序，不做无目的全 RAM 扫描：

1. 选一个可重复动作（按键、进门、打开菜单、草地一步、开始战斗）；
2. capture 前后各一张图；
3. 用同一 bridge frame 的 bounded `memory_batch_snapshot`；
4. 计算 byte/word/bit diff、稳定区间、指针候选、计数器单调性；
5. 重复至少两次，加入负对照（不应改变的状态）；
6. 只有通过正/负对照，才把字段加入 decoder；否则保留 raw candidate。

### 13.3 失效和版本隔离

ROM hash、bridge version、session_id、savestate load、软重启、地图切换都会触发 registry
校验。指针链失效时 API 返回 `stale_pointer`/`rediscovery_required`，禁止继续使用旧地址。
所有解释器都必须支持“只显示 raw，不显示错误语义”的降级模式。

## 14. 存档、真实本机保存和回滚（新增）

要区分三种持久化：

1. **BizHawk savestate**：模拟器状态快照；可快速回滚实验，但不等于游戏真实存档。
2. **游戏内保存**：ROM 写入 SRAM/Flash 的存档块；要观察保存开始/完成、槽位、checksum、
     backup/active block、游玩时间和 flag 是否落盘。
3. **项目 evidence**：本机 JSON/PNG/RAM bundle；只记录证据，不修改游戏。

计划新增只读 `save_storage` 视图：

```json
{
  "savestate": {"slot": 2, "status": "confirmed|unresolved", "frame": 0},
  "game_save": {"domain": "SRAM|Flash", "status": "candidate", "active_block": null,
                 "checksum": null, "dirty": null, "last_save_event": null},
  "rom_hash": "...",
  "session_id": "...",
  "writes_performed": false
}
```

真实存档写入只在用户明确要求并完成备份后考虑；本阶段只做保存动作前后的只读观察。加载
savestate 后必须重新采样 Player/flags/party/bag，并把旧导航 task、prepared action 和上下文
标为 stale。任何“加载失败但弹窗被关闭”的场景都保存完整 failure evidence。

## 15. 影响推进的额外游戏系统（新增）

以下系统不一定第一天全部解码，但应预留字段、事件和测试槽位：

- **RTC/时间/季节**：真实时钟、昼夜、季节切换、周事件；不要从 PC 时间直接猜游戏值。
- **遇敌计步和 RNG**：草地/水面/洞窟 encounter counter、Repel、能力影响、随机种子只记录
  candidate；未验证概率不能用于规划保证。
- **升级/进化/奖励**：经验增量、升级、进化提示、金钱/道具奖励、训练家战后 flag；动画期间
  可提前读 candidate，但要等 result/settlement 才提交最终值。
- **互动与运动**：门、楼梯、跳台、传送带、旋转地板、滑冰、桥/上下层、钓鱼、冲浪上岸、
  自行车禁区；每种都要记录方向、前置条件、落点和是否消耗输入。
- **天气和场地**：Zone 默认天气、战斗实时天气、天气倒计时、场地效果、能力/招式触发；
  静态 ZoneHeader 永远不能替代 battle runtime。
- **NPC/事件身份**：sprite/script/movement/flag/trainer profile、视线遮挡、战后状态；
  普通 NPC 不因 sight_raw 自动判定为训练家。
- **菜单和双屏**：上/下屏、触摸热点、箭头 cursor、分页、确认/取消、文本打印器；截图和
  RAM 分开标注，不能只凭像素坐标驱动长序列。

## 16. 运行时恢复和“不会卡死”策略（新增）

AI 任何时候都要能回答“现在是否还能安全继续”：

- bridge 断开、EmuHawk 暂停、弹窗阻塞、ROM 未加载、session 改变、pointer invalid、
  snapshot 过期、动作 lease 被占用，都进入明确的 `blocked_reason`；
- 自动导航每步设置 watchdog：连续若干 frame GPos 不变、朝向变化但未位移、黑屏超时、
  预期 Zone 未到达时暂停并保存证据；
- 只允许从最近 confirmed savestate 或用户指定 checkpoint 恢复；不得自动覆盖用户真实存档；
- 重试必须使用新的 observation/correlation_id，禁止重复提交旧 action；
- API 错误响应包含 `retryable`、`requires_user`、`safe_to_resume`，让便宜模型也能安全处理。

## 17. 上下文文件的推进优先级（新增）

在 `focus.json` 增加 `progression_priority` 和字段白名单：

```json
{
  "focus": "gym_navigation",
  "objective": "打开道馆机关并到达馆主前",
  "required_context": ["current_state", "story_gates", "warps", "terrain", "npc_hazards"],
  "optional_context": ["party_summary"],
  "excluded_context": ["full_global_tiles", "opponent_roster"],
  "checkpoint": "savestate:3"
}
```

`current_state.json` 只保存当前 session/frame/layer/Zone/GPos/目标/动作 owner；
`short_term.ndjson` 保存最近 20–50 个 case 的结论和证据路径；`long_term.md` 只保存已验证
协议和稳定地址，不写入一次性猜测。剧情切换、进入战斗、进入道馆、遇到未知菜单时自动换
focus，并保留前一 focus 的摘要而不是整段日志。

## 18. 新增验收门槛和交付顺序

在原 M0–M5 之前增加两个不可跳过的门：

- **M-1 证据/存档安全**：capture、bounded RAM、savestate、SRAM 只读观察、session 失效、
  surrogate/JSON 安全；所有未知状态能返回 JSON，不出现 500。
- **M0.5 推进状态**：故事 flag/脚本、objective、gate、party/bag/关键道具、PC/钱/徽章至少
  有清晰的 `unresolved/candidate` API 外壳和前端显示。

交付顺序建议：

```text
证据和生命周期 → 当前画面状态 → 推进/故事 gate → 导航与互动
→ 野外遇敌 → 训练家/道馆战斗 → 战斗只读状态机
→ 单动作战斗写入 → 一键技能/道具/投球 → 长时 AI playtest
```

任何阶段都必须保留 raw evidence、解释版本和回滚点；若一个新 decoder 不能说明自己的
来源、frame、ROM/session 适用范围和失败条件，就不能作为自动行动依据。
