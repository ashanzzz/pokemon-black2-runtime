# 宝可梦黑 2 AI API 目录（V18）

本目录描述当前项目中“可以让 AI 做决定”的稳定入口。完整的机器可读目录始终以运行中的 `GET /openapi.json` 为准；启动后可打开 `/docs` 查看参数与响应模型。本文件补充端点之间的调用顺序、证据边界和已经验证/仍待验证的能力，避免 AI 只看到了一个能返回 200 的接口就误以为动作已经成功。

## 1. 统一决策循环

每次准备写入游戏前按下面顺序读取：

1. `GET /api/v1/agent/observe`：一次性读取面向 Pokémon Agent 的紧凑观察；需要深入证据时再读取它返回的资源 URL。
2. `GET /api/v1/player/runtime`：解析后的 Zone/GPos、朝向、运动阶段、session/frame。
3. `GET /api/v1/game/current` 或 `GET /api/v1/game/environment`：剧情/场景/对话/战斗的语义状态。
4. `GET /api/v1/agent/events?after=<cursor>`；长等待使用 `/api/v1/agent/events/wait`，重启后查 `/api/v1/agent/events/log`。
5. 只有状态仍然允许输入时，才调用规划或执行 API；执行后用 PlayerRuntime 和事件确认结果。

事件流不是“另一个状态源”：事件用于告诉 AI 哪一类状态刚刚变化，完整状态仍从资源 URL 重新读取。游标过期时，立即读取 `/api/v1/agent/state` 和相关资源，不要继续猜测旧状态。

### 等待边界（Wait Boundary）

RuntimeHub 仍然按固定间隔采样 RAM，但 AI 不需要逐帧参与。每次语义等待边界变化只发送一次 `runtime.wait.changed`，事件的 `data.event_window` 会压缩记录上一个等待点到当前等待点之间发生的事件、动作请求和结果。事件日志同时写入 `runtime/ai_context/agent_events.ndjson` 与短期记忆，适合另一个 AI 一次性读取。

| 等待类型 | `wait_state.kind` | 规则 |
| --- | --- | --- |
| 自动过场 | `auto_transition` | 对话页已打印完等可按一次 A；调用 `/api/v1/agent/wait/advance` 后等待下一事件。文字打印中、切图或战斗动画只等待状态变化，不盲按。 |
| 需要决策 | `decision` | 对话选项、战斗命令/招式、菜单等冻结自动推进；AI 读取 `actions` 后选择合法动作。 |
| 未解析/不安全 | `hold` + `status=unresolved` | 不发送输入，先读取对应资源或保存证据。 |

`GET /api/v1/agent/wait-state` 返回当前 `wait_id`、语义原因、允许动作和事件游标。自动动作必须回传同一个 `wait_id`；等待边界已变化或当前不是 `auto_transition + press_A_once` 时，服务端返回 `409`，因此重复/延迟请求不会多按一次 A。当前已实机验证对话翻页；战斗升级/奖励/战斗消息只有在对应 RAM 状态完成验证后才会加入自动过场白名单。

自动过场还有一层证据门：已校准的完整加载文本可以通过内容 SHA-256 绑定到具体合同（例如 `EXP_012_zone446_gate_no_badge_dialogue`），合同只授权有限次数、固定最小间隔的单步 A，并在每步后重新读取等待边界。未知或 `TextPrinter` 未解析的文本即使看起来像普通对话，也保持 `hold`/`wait_for_state_change`，不会因为截图或固定步数而自动连按。

## 2. 状态、记忆、日志和证据

| 用途 | API | 说明 |
| --- | --- | --- |
| 运行时总快照 | `GET /api/v1/runtime/snapshot` | RuntimeHub 快照；诊断和前端使用。 |
| AI 状态索引 | `GET /api/v1/agent/state` | 输入归属、battle/dialogue attention、事件游标和资源。 |
| 玩家状态 | `GET /api/v1/player/runtime` | RAM/结构解析优先的当前玩家位置与移动状态。 |
| 当前游戏状态 | `GET /api/v1/game/current` | 标题、菜单、场景、对话、战斗等语义状态。 |
| AI 三层记忆 | `GET /api/v1/ai/memory` | 长期架构事实、中期当前剧情/背包/队伍、短期事件。 |
| 写入记忆事件 | `POST /api/v1/ai/memory/event` | 只写有证据的观察；长文本应保留来源和 case_id。 |
| 同步运行时 | `POST /api/v1/ai/memory/sync` | 将当前 runtime/player/party/inventory/objective 写入中期记忆。 |
| 实时事件读取 | `GET /api/v1/agent/events?after=&limit=` | 游标增量读取。 |
| 实时事件等待 | `GET /api/v1/agent/events/wait?after=&timeout_ms=` | 长轮询，适合脚本。 |
| 事件 SSE | `GET /api/v1/agent/events/stream?after=` | 前端/外部监控使用。 |
| 事件持久尾部 | `GET /api/v1/agent/events/log?limit=` | 重启安全的 NDJSON 尾部；文件为 `runtime/ai_context/agent_events.ndjson`。 |
| 当前等待边界 | `GET /api/v1/agent/wait-state` | 不发起新 RAM 读取；返回 `wait_id`、自动/决策分类和上下文。 |
| 自动过场一步 | `POST /api/v1/agent/wait/advance` | Body `{"wait_id":"..."}`；只允许一个已授权 A，不接受批量步数。 |
| 证据执行器 | `tools/ai_playtest_evidence.py` | 一次动作 + API/RAM 状态；语义不一致或显式要求时才截图。 |

记忆文件位置：`runtime/ai_context/memory_state.json`、`long_term.md`、`short_term.ndjson`、`memory_registry.json`。Playtest case 默认在 `runtime/evidence/ai_playtest/`，未解决问题的 RAM/API/截图索引应与 case 一起保存。

### 2.1 统一观察契约：`GET /api/v1/agent/observe`

这是未来 Pokémon Agent 的首选只读入口。请求只取一次 `RuntimeHub.snapshot()`，随后附加有界的 ROM 场景、ActorSystem、持久队伍和服务目录读取；不会发送按键、触控或写 RAM。默认返回紧凑字段，`include_raw=true` 才附带受控的原始证据。

响应 `format=black2-agent-observation/v1` 的主要结构：

| 字段 | 内容 |
| --- | --- |
| `observation` | frame、session、freshness、事件 cursor/state revision。 |
| `state` | `primary_context`、非互斥 `active_layers`、输入归属、`wait_state` 和当前允许动作。 |
| `player` | Zone、grid/world 坐标、朝向、运动/交通方式及 PlayerRuntime 证据。 |
| `world` | 当前 Zone label、header/matrix/rules、附近静态 NPC、实时 Actor、Warp、交互候选和覆盖范围。 |
| `dialogue` | speaker/text/loaded text/choices/active pointer；未解码字段保持 `null`。 |
| `battle` | BusyFlag/战斗候选、缓存 UI 游标、合法操作状态和 identity/party/moves 资源入口。 |
| `party` / `inventory` | 队伍和背包的语义投影；当前队伍已可通过 `GameData.PokeParty` 校验，背包仍 unresolved。 |
| `story` | objectives、flags、cutscene 和长/中/短期记忆摘要。记忆不是 ROM flag 真值。 |
| `actions` | `navigate_to`、`talk_to_actor`、对话、战斗、恢复、Warp 等动作合同；每项包含前置条件、完成条件、失败码、验证资源和三态 `can_execute`。 |
| `events` | 最近事件、cursor、state revision 和下一次增量等待入口。 |
| `missing_api` | 当前仍影响独立 Pokémon Agent 的能力缺口及优先级。 |

`status=current` 只表示当前 RuntimeHub 观察新鲜；`partial` 表示静态世界可读但实时状态已过期；`unresolved` 表示不能安全理解当前状态。空的 `slots/items/npcs` 不代表游戏中没有对应资源。该端点是状态聚合器，不把读取 API 偷换成动作 API；所有真正写入仍需通过导航、故事自动化、等待或战斗动作端点，并在之后重新观察确认。

## 3. 巡路（Navigation）API

巡路是“去一个已知坐标/目标”的能力，不负责随机遇怪。当前坐标统一使用 `type=grid`、`space=gen5-field-grid-v1`、`zone_id,x,y,z`；`y` 是高度层，不能省略。

| 阶段 | API | 作用 |
| --- | --- | --- |
| 能力 | `GET /api/v1/navigation/capabilities` | 坐标、同 Zone/跨 Zone、走路/跑步/自行车/冲浪和证据状态。 |
| 上下文 | `GET /api/v1/navigation/context` | 当前 Zone、动态 actor、输入限制和规划上下文。 |
| 障碍 | `GET /api/v1/navigation/hazards?radius=` | NPC/脚本/Warp/单向 barrier/地形限制候选。 |
| 规划 | `POST /api/v1/navigation/plans` | 只读规划；不发送输入。请求明确 `destination`、`movement_mode`、`navigation_intent`。 |
| 执行 | `POST /api/v1/navigation/tasks` | 异步闭环逐格执行；返回 `202` 和 task id。 |
| 状态 | `GET /api/v1/navigation/tasks/{task_id}` | 当前格、重规划、验证帧、终态和 stop_reason。 |
| 取消 | `POST /api/v1/navigation/tasks/{task_id}/cancel` | 清理输入并停止任务。 |
| 观测图 | `GET /api/v1/navigation/observations?...` | 只返回直接观测过的同 Zone 有向边，不等于可执行全图。 |
| Warp 证据 | `GET /api/v1/navigation/warp-evidence` | 实机 `source_zone/grid -> destination_zone/landing_grid` 记录。 |
| 规划/执行日志 | `GET /api/v1/navigation/logs?...` | 读取结构化巡路日志。 |

方向控制规则：单次方向输入首先可能只改变朝向；只有请求方向已经与当前朝向一致时才移动。因此调用方不要把“按了一次方向”当成“前进一格”。执行器必须用下一帧 PlayerRuntime 校验落点，不一致就停下、重读状态或截图诊断。

`walk`、`run`、`bike`、`surf` 已进入 schema；自动化目前默认 `auto`，自行车/冲浪只有在运行时已具备对应状态且有验证证据时才可提升。跨 Zone 执行不会因为静态地图存在 Warp 就自动开放，必须先有 live warp evidence。

常用的短距离校准可使用 `tools/ai_verified_walk.py`：它按当前 PlayerRuntime 逐格发出有限动作，每一步都重新读取落点；遇到“只转向未移动”、高度层变化或预期落点不一致会立刻停止，并在 `runtime/evidence/ai_playtest/` 保存语义状态、bounded RAM 和必要截图。输入写入必须串行，不能同时启动多个会写入游戏的脚本，否则共享输入队列会使朝向/落点证据失效。

观测图可以记录实机确认的 `vertical_same_tile`（同一 X/Z 的高度层转换），但这不等于静态规划器已经能推断所有楼梯/斜坡。规划器没有直接观测边或高度层证据时应返回 `NAV_NO_ROUTE`，调用方应先用短段校准或提供已观测航点，不能把静态候选当成可通行路线。

目前 `vertical_same_tile` 只作为逆向证据保存，尚未映射为可执行的方向/持续时间合同；执行器会主动排除这类未经验证的边，避免把“同一 X/Z 的高度变化”误当作普通平地一步。

## 4. 自己遇怪（Encounter）API

Encounter 与 Navigation 分开：Encounter 只选择已确认的遇怪区域并复用巡路执行；它不负责剧情 NPC，不负责任意跨地图，不把“离开 OVERWORLD”直接声称为成功战斗。

| 阶段 | API | 作用 |
| --- | --- | --- |
| 能力 | `GET /api/v1/encounters/capabilities` | 草地、水面候选、物种表和战斗观察器的证据等级。 |
| 当前区域 | `GET /api/v1/encounters/regions/current?connected=&include_water_candidates=` | 读取当前 Zone 或同 Matrix 的物理区域。 |
| Zone 区域 | `GET /api/v1/encounters/regions?zone_id=&y=` | 精确 Tile 集、入口/内部 Tile、轮巡建议。 |
| 单区域 | `GET /api/v1/encounters/regions/{region_id}?zone_id=&y=` | 读取一个区域。 |
| 物种 profile | `GET /api/v1/encounters/profiles?zone_id=` | 当前仍是研究/未验证时明确返回 research。 |
| 物种搜索 | `GET /api/v1/encounters/search?pokemon_id=` | 未完成 ROM encounter mapping 时返回空候选，不编造概率。 |
| 开始自遇怪 | `POST /api/v1/encounters/tasks` | `ping_pong`、`loop`、`line_shuttle`；仅同 Zone、区域 Tile 内巡路。 |
| 任务状态/取消 | `GET /api/v1/encounters/tasks/{task_id}`、`POST /api/v1/encounters/tasks/{task_id}/cancel` | 安全停止优先。 |

草地目前是 verified physical region；水面是 probable Surf candidate。遇怪物种、计步/RNG、战斗菜单自动选择仍未完成，不能为了“通关”盲按技能。

## 5. 高层故事自动化 API

这是“一个 API 完成常用流程”的薄编排层，内部仍使用上面的闭环巡路和事件/记忆系统。

| API | 作用 |
| --- | --- |
| `GET /api/v1/agent/automation/capabilities` | 查看恢复、NPC 对话、PC 候选和选择策略。 |
| `GET /api/v1/agent/services/nearby?service_type=recovery` | 查当前 Zone 的可用恢复服务；`service_type=nearest|all|pc_storage` 可筛选。 |
| `POST /api/v1/agent/automation/recover` | 自动寻找已验证的恢复点，导航到护士，交互并连续按 A；遇到选项暂停。 |
| `POST /api/v1/agent/automation/interact` | 以 `npc_id`、`script_id`、完整坐标或 service selector 找 NPC，巡路、交互、记录对话。 |
| `GET /api/v1/agent/automation/tasks/{task_id}` | 查询高层任务阶段、子巡路任务、对话文本和 stop_reason。 |
| `POST /api/v1/agent/automation/tasks/{task_id}/cancel` | 停止高层任务并取消巡路子任务。 |

NPC 对战状态单独使用只读证据链，不把静态“训练家候选”当成已经对战：

| 能力 | API/脚本 | 说明 |
| --- | --- | --- |
| 每个 NPC 的对战候选与原始状态 | `GET /api/v1/ai/map/npc-battle-status?zone_id=` | 合并 ROM NPC/sight/script、当前 FieldActor 绑定及 `flags_raw/spawn_flag_raw/script_id_raw/朝向`；`capability`、`runtime`、`lifecycle.defeat_status` 分层返回。每个已探测 NPC 还会附 `battle_observation.last_probe`（结果、对话、frame、RAM diff）；当前事件 flag decoder 未验证时，缺 actor 或 flag 变化都只能是 `unknown`。 |
| 地图脚本函数与实体绑定 | `GET /api/v1/ai/map/scripts?zone_id=&script_index=` | 读取当前 ROM 的 `a/0/5/6` 脚本指针表，返回脚本索引/函数边界、NPC/触发器/家具的静态 `script_id` 绑定、受控 opcode 候选及 TrainerBattle 候选；`Message.Actor/ActorEx` 仍是 word-level candidate，不能据此声称脚本已经执行或绑定了某个 NPC。 |
| 下一批探测队列 | 同上返回的 `probe_queue` / `probe_candidates` | `probe_candidates` 只保留未探测、被已有模态层阻塞或历史未解析的视线候选；`observed_dialogue_without_battle` 和 `uncertain_no_battle_transition` 只留在审计队列，不在每次轮询中重复触发。 |
| 前后内存差分历史 | `GET /api/v1/ai/map/npc-battle-status/history?limit=` | 读取 `runtime/ai_context/npc_battle_observations.ndjson` 的有界记录；同时保留 `probe_status/probe_failure`，把不可达、已有模态层和真实交互分开。 |
| 写入一次前后观察 | `POST /api/v1/ai/map/npc-battle-status/record` | 只记录调用方提供的 before/after，不按接口调用修改游戏 RAM，也不会自行标记胜利。 |
| 可复用探测脚本 | `tools/ai_npc_battle_probe.py` | 默认只读；`--execute` 只运行现有 NPC 交互，遇到战斗边界即停止，不发送战斗指令。脚本会等待延迟出现的战斗/对话模态层，并把 API/RAM 证据写入 `runtime/evidence/ai_playtest/`。 |

判断规则：ROM `sight_raw > 0` 只是视线战斗候选；同 Zone 的 `TrainerBattle` 只是脚本候选；FieldActor `flags_raw` 是生命周期研究字段；只有同帧战斗因果绑定，或经过验证的 trainer-specific event flag/before-after 结果，才可以把 NPC 标成“已对战/已击败”。道具球等非 NPC 事件不会进入该接口的 NPC 列表。

当前已验证登记：Zone 443（算木镇精灵中心室内）的护士 `script_id=2100`；Zone 439 ↔ 443 的恢复连接器只有在 `warp-evidence` 记录存在时才自动使用。电脑 `script_id=2108` 仅作为 candidate，`execution_available=false`，直到 PC 菜单/队伍盒 RAM 和按键语义完成校准。

当 `interact` 使用 `npc_id` 或 `script_id` 选择器时，HTTP 路径会先读取一次 bounded ActorSystem：选择器必须绑定到当前场景的 live actor，目标坐标以 live `grid` 为准；静态 ROM 坐标只作为追踪证据。模型 97/98/99 等动态障碍以及 `script_id>=7000`/物品模型会在发出任何输入前返回 `AUTOMATION_TARGET_OBSTACLE` 或 `AUTOMATION_TARGET_ITEM`。没有 live 绑定时返回 `AUTOMATION_NPC_RUNTIME_UNRESOLVED`，不会盲目寻路或按 A。显式完整坐标仍适合已校准的固定设施，但也必须通过输入归属和落点校验。

已记录的例外也必须保持证据约束：Zone 439 的 `script_id=8/model_id=97` 虽然静态记录曾被识别为障碍物，但实时 ActorSystem、截图和 RAM 已共同确认它是牧场入口的剧情角色；只有同一 bounded live actor 绑定同时存在时才允许作为剧情 NPC，缺少绑定仍按障碍物拒绝。

自动对话只在对话层确认 active 后按 A；每段文本去重写入任务记录、事件日志和短期记忆。出现 choices 时默认 `stop`，不会猜选项。

## 6. 对话、战斗和低层动作边界

- 只读对话：`GET /api/state`、`GET /api/dialogue/history`、`GET /api/dialogue/checkpoints`。
- 对话动作：`POST /api/actions/dialogue/advance` 仍受旧安全门约束；优先使用高层 `automation/interact`，让输入归属和闭环验证统一。
- 低层输入：`POST /api/actions/press`、`POST /api/actions/hold` 等仅用于校准、已验证连接器和证据脚本；不要绕过 NavigationTaskService 直接长按走远路。
- 战斗：`GET /api/v1/battle/state`、`GET /api/v1/battle/capabilities`、`GET /api/v1/battle/request`。当前战斗观察与动作决策仍按 `observed/research` 分级，未知时停止。身份专用接口如下：

  | API | 作用与证据边界 |
  | --- | --- |
  | `GET /api/v1/battle/identity` | 读取当前 active battle 的 `btl_pokeparam.c` 对象；从 RAM 解出种族编号，再映射本地 Black 2 Dex，返回对手/我方宝可梦名字、HP 候选、原始地址和 frame。战斗跃迁同时保留 `causal_context`：在同一帧从 ActorSystem 恢复的最近非玩家 FieldActor（uid/坐标/model/script/event）。另读 bounded `0x0224B000..0x0224D000` 的 `btl_setup.c`→`strbuf.c`，把 UTF-16LE/`0xFFFF` 训练家名与本地 TRData/TRPoke 目录、当前对手种族交叉匹配；因此可在没有直接 trainer_id 指针时报告姓名/ID 的 `candidate`，但仍不标成 verified。`candidate` 不是 BattleMon/等级/触发指针已验证。 |
  | `GET /api/v1/battle/identity/history` | 读取 RuntimeHub 在战斗开始时保存的最近遭遇身份；战斗结束后当前 RAM 不再扫描旧堆，使用这个接口查看最后一次野生/训练家候选、训练家名/类别和对手宝可梦名。日志保留 `candidate`/`unresolved`，不把静态目录冒充 live 事实。 |
  | `GET /api/v1/battle/trainer/{trainer_id}` | 只读 ROM 目录：`a/0/9/1` TRData、`a/0/9/2` TRPoke、文本 `a/0/0/2` 的 382/383 号成员，返回训练家姓名、训练家类别、战斗形式、队伍种族/等级/招式候选。它是静态目录，不等于当前对手。 |
  | `GET /api/v1/battle/zone/{zone_id}/trainer-candidates` | 解析该 Zone 的地图脚本 `a/0/5/6`，提取 `SingleTrainerBattle`/`DoubleTrainerBattle`/`TrainerBattle`，归一化常见的 `0x400` 编码并回填训练家姓名/类别/队伍；这是静态候选表，不等于已经触发。 |
  | `GET /api/v1/battle/state` | 将 `identity` 嵌入状态；RuntimeHub 优先保存同 session 的 Zone/坐标和 ActorSystem 因果候选；若后端在战斗中重启且 PlayerRuntime 链暂不可见，会执行一次只读 Main RAM ActorSystem 恢复。只有真实 trainer_id、当前战斗 `strbuf.c` 名称+TRData/TRPoke 队伍匹配，或唯一脚本候选匹配时才返回训练家姓名的 `candidate` 证据；多候选或无因果绑定时保持 `unresolved`，不再把有邻近 NPC 的训练家战斗误报为野战。 |
  | `GET /api/v1/battle/ui-capabilities` | 暴露菜单光标 RAM 读回验证状态；当前仍是 `diagnostic_only_memory_gate`。固定 `Up→Left→Down` 不可作为执行依据。 |

  当前已能回答“遇到的宝可梦是什么”：例如 live `btl_pokeparam.c` 的 `payload + 0x18` 种族字段 `504` 会映射为 Patrat；训练家身份先绑定同帧 ActorSystem→NPC 候选，再优先使用 runtime trainer_id，或使用当前战斗 `btl_setup.c`/`strbuf.c` 的真实 UTF-16 名字与 TRData/TRPoke 队伍交叉匹配，最后才使用唯一 Zone 脚本候选。RuntimeHub 会在战斗开始时把 RAM→Actor→Dex/ROM 证据写入 `runtime/ai_context/battle_identity_history.ndjson` 和 agent event；战斗结束后从 `/api/v1/battle/identity/history` 读取。没有唯一候选时不从截图、地图或物种反推。`level_or_exp_raw` 仍保留原始值，直到受控等级扫表验证前不标成等级。

旧版工作台/地图/调试、RAM dump、savestate、启动器等仍由 OpenAPI 列出；它们是诊断或维护入口，不会自动获得剧情执行权限。所有写入动作都应先通过 `/api/v1/agent/state`、PlayerRuntime 和事件确认输入归属。

## 7. 证据与失败处理约定

成功不是 HTTP `200/202`，而是：请求被接受 → 子任务逐格验证 → 目标 Zone/GPos 与预期一致 → 交互/对话事件结束。失败要保留 `code`、`details`、`task_id`、`case_id`、最近事件游标和 session/frame。

截图只做语义不一致时的诊断证据；如果 API/RAM 与截图不一致，优先保留三者，标记 `unresolved`，不要覆盖成“看起来正确”。无法解决的问题写入 playtest case，并导出 bounded RAM、runtime snapshot、事件日志和截图，供另一个 AI 继续读取。

| 完整 AI 场景 | `GET /api/v1/ai/scene/complete?zone_id=&radius=` | 官方区域名/环境、玩家、ROM 静态实体、可选 ActorSystem 动态实体、交互候选、脚本候选和证据边界的一次性聚合。 |
