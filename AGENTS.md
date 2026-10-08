# Pokémon Black 2 Autonomous Agent 研发与逆向参考指南 (AGENTS.md)

本文件是本项目所有 AI Agent、自动化测试与代码生成的主指导规范（Ground Truth & Guiding Principles）。任何在此仓库中开展工作的 Agent 必须严格遵守并深度参考本指南。

---

## 一、 核心工程准则 (Core Principles)

0. **终极项目定位与职责边界：全力暴露全量 API，赋能外部 AI，绝不越界优化游戏流程 (Full API Exposure & Capability Oracle, No Gameplay Workflow Optimization)**：
   - **本项目唯一核心使命**：构建 Pokémon Black 2 的底层基础设施与全功能 API 暴露层（Game API / Memory Oracle / Actuator）。将底层的 ARM9 RAM 结构体、2D/3D 空间地图与多层碰撞切片、确定性 A* 寻路与步态控制、队伍/背包/图鉴真值、战斗状态机与动作执行、虚拟手柄按键与触控时序等，**无死角、全透明地暴露为结构化、确定性的标准化 API**（REST / MCP / WebSocket）。
   - **坚决不对 AI 打游戏流程进行业务优化**：本项目**绝不**负责优化 AI 打游戏的业务逻辑、推关套路、配招流派或自动化策略。打游戏的业务决策权完全属于外部调用方 AI。
   - **保证其他项目的 AI 可以各种方式理解并操作游戏**：本项目的所有设计、Web UI、路由与文档，必须全力保障让其他项目或模型的 AI（无论是纯文本 LLM、多模态 Agent、还是自动化脚本）能够清晰、详尽、低延迟地获取游戏全部上下文，并能通过原子或组合 API 精准、安全地操控游戏。所有 UI 界面首要职责是作为“全功能 API 交互大屏与调试能力目录 (API Dashboard & Capability Deck)”，真实直观地呈现所有 API 的输入输出与底层回读。

1. **坚决不做“截图识别+盲猜按键”的假 AI (No CV/OCR Fragility)**：
   - 严禁依赖视觉多模态截图或 OCR 猜按键。
   - 所有游戏状态、环境感知、战斗属性、背包库存、队伍数据均来自 **底层 ARM9 RAM 结构化反向解码**。
   - 截图仅作为人类开发者肉眼 Debug 和 UI 标定的辅助证据，绝不作为核心状态机的输入。

2. **分层架构与职责划分**：
   - **AI (LLM) 决策层**：仅负责宏观高层意图（如 `ChallengeGym(Cheren)`, `CatchWildPokemon`, `HealAtPokeCenter`, `UseItemInBattle`）。
   - **确定性控制器 (Game API / Pathfinder / ActionEngine) 层**：处理微观 A* 寻路、逐格奔跑、碰撞/跳台、下屏触摸坐标、按键时序、文本框状态机与结果回读。

3. **单一权威 GameState (Single Authoritative State)**：
   - 系统中只能有一个权威状态（`GameState`），所有 API、WebSocket/SignalR 推送与前端 UI 同源派生，严禁多头读写。

4. **Single Writer 单写入线程与租赁锁**：
   - 模拟器输入由 `InputLease` 互斥保护，所有外部指令进入确定性队列，杜绝并发竞争。

---

## 二、 重点参考与深度借鉴的开源成熟生态 (Ecosystem References)

开发过程中必须严重参考以下成熟开源项目的逆向结论、结构体定义与设计范式：

### 1. 模拟器与外部控制核心
- ⭐⭐⭐⭐⭐ **BizHawk** (`https://github.com/TASEmulators/BizHawk`)：
  - **核心借鉴**：Memory Domain（Main RAM, ARM9 BIOS）、Frame Control、Savestate、External Tool 扩展机制。
- ⭐⭐⭐⭐⭐ **BizHawk External Tools Wiki** (`https://github.com/TASEmulators/BizHawk-ExternalTools/wiki`)：
  - **核心借鉴**：EmuHawk C# API、UI 线程 marshaling、高频内存采样优化。
- ⭐⭐⭐⭐⭐ **bizhawk-mcp-native** (`https://github.com/stealthc/bizhawk-mcp-native`)：
  - **核心借鉴**：LLM → Tool → Emulator 架构、批量原子内存操作（`memory.read_batch`）、版本锁定策略。

### 2. Gen 5 ROM 与地图/事件解析
- ⭐⭐⭐⭐⭐ **SwissArmyKnife** (`https://github.com/PlatinumMaster/SwissArmyKnife`)：
  - **核心借鉴**：Gen 5 ROM 容器格式（a/0/1/2 等 NARC）、ZoneHeader、AreaHeader、Matrix 矩阵、Collision 碰撞模型、NPC、Warps、Triggers、野生遇敌表。
- ⭐⭐⭐⭐ **CTRMap-CE** (`https://github.com/PlatinumMaster/CTRMap-CE`)：
  - **核心借鉴**：Gen 5 地图编辑器、事件脚本指令集数据库（Script Commands）、3D 建筑与地形模型。
- ⭐⭐⭐⭐ **ndspy** (`https://github.com/RoadrunnerWMC/ndspy`)：
  - **核心借鉴**：Nintendo DS 通用二进制格式、NARC 解包与字节级解析。

### 3. 底层动态内存逆向与数据模型
- ⭐⭐⭐⭐⭐ **swan** (`https://github.com/ds-pokemon-hacking/swan`)：
  - **核心借鉴**：Black 2 / White 2 官方符号库（ESDB）、C 源码结构体（`btl_pokeparam.c`、`btl_setup.c`、`FieldStatus`、`GameData`、`PokeParty`、`Bag`）、内存函数地址。
- ⭐⭐⭐⭐ **PKHeX** (`https://github.com/kwsch/PKHeX`)：
  - **核心借鉴**：Pokémon 核心数据模型、队伍解密与 16 位校验和算法、个体值/努力值/性格/PID、技能与道具 ID 表。
- ⭐⭐⭐⭐ **PokéBot** (`https://github.com/Kakumi/Pokebot`)：
  - **核心借鉴**：宝可梦游戏自动化内存读取、战斗逻辑与自动化决策机制。

### 4. 静态逆向与代码注入
- ⭐⭐⭐⭐ **NTRGhidra** (`https://github.com/pedro-javierf/NTRGhidra`)：
  - **核心借鉴**：针对 NDS ARM9 与动态 Overlay 的 Ghidra Loader。
- ⭐⭐⭐ **PMC** (`https://github.com/ds-pokemon-hacking/PMC`) & **White2Upgrade** (`https://github.com/ds-pokemon-hacking/White2Upgrade`)：
  - **核心借鉴**：B2W2 运行时代码注入、函数 Hook、后期在游戏内注入 Agent 调试状态。

---

## 三、 黑2实战宝可梦内存结构体速查表 (`btl_pokeparam.c`)

在 IREJ01 主内存堆区（`0x0225B000` 窗口），每个参战宝可梦（我方出战、敌方出战）均由 `btl_pokeparam.c` 分配对象维护（总长 532 字节 `0x214`，头部 32 字节，Payload 500 字节）：

| 偏移 (相对于 Payload) | 类型 | 语义 | 说明与实机示例 |
| :--- | :--- | :--- | :--- |
| `+0x14` | u32 | 经验值 (EXP) | 水水獭=516，探探鼠=125 ($5^3$, Lv.5) |
| `+0x18` | u16 | 宝可梦物种 ID | 501=水水獭，504=探探鼠 |
| `+0x1A` | u16 | 最大生命值 (Max HP) | 水水獭=31，探探鼠=20 |
| `+0x1C` | u16 | 当前生命值 (Current HP) | 实时变化（受伤害/恢复） |
| `+0x22` | u16 | 特性 ID (Ability) | 67=激流 (Torrent)，51=锐利目光 (Keen Eye) |
| `+0x24` | u8 | 真实战斗等级 (Level) | `0x09`=Lv.9，`0x05`=Lv.5 |
| `+0x25` | u8 | 性别 (Gender) | `0`=♂ (雄性)，`1`=♀ (雌性)，`2`=无性别 |
| `+0xFA` | u16 | 实战物理攻击 (Attack) | 水水獭=18，探探鼠=11 |
| `+0xFC` | u16 | 实战物理防御 (Defense) | 水水獭=12，探探鼠=9 |
| `+0xFE` | u16 | 实战特殊攻击 (Sp. Atk) | 水水獭=17，探探鼠=8 |
| `+0x100` | u16 | 实战特殊防御 (Sp. Def) | 水水獭=14，探探鼠=9 |
| `+0x102` | u16 | 实战速度 (Speed) | 水水獭=15，探探鼠=9 |
| `+0x108 ~ +0x10E` | 7 字节 | 7 维能力阶级 (-6 ~ +6) | 攻/防/特攻/特防/速/命中/闪避（基准为 6，即 0 阶） |
| `+0x110` | 14 字节 | 第 1 技能槽 | `+0x00`: MoveID (u16), `+0x02`: CurPP, `+0x03`: MaxPP |
| `+0x11E` | 14 字节 | 第 2 技能槽 | `+0x00`: MoveID (u16), `+0x02`: CurPP, `+0x03`: MaxPP |
| `+0x12C` | 14 字节 | 第 3 技能槽 | `+0x00`: MoveID (u16), `+0x02`: CurPP, `+0x03`: MaxPP |
| `+0x13A` | 14 字节 | 第 4 技能槽 | `+0x00`: MoveID (u16), `+0x02`: CurPP, `+0x03`: MaxPP |

---

## 四、 Agent 核心 REST API 查阅表

Agent 与确定性控制器交互的标准接口集合：

1. **战斗综合状态**：`GET /api/v1/battle/state`
2. **参战双方完全体档案**：`GET /api/v1/battle/identity`（含敌我物种、等级、性别、特性、五维能力、能力阶级、技能槽与实时 PP）
3. **技能槽位与 PP 专用查询**：`GET /api/v1/battle/moves?actor=player:0` 或 `actor=opponent:0`
4. **野生捕获评估**：`GET /api/v1/battle/capture-eval`（满血/残血、持有球、物种捕获率与决策推荐）
5. **战斗动作执行**：`POST /api/v1/battle/ui-actions`
   - 使用技能：`{"type": "use_move", "actor": "player:0", "move_slot": 1}`
   - 投掷精灵球：`{"type": "throw_ball", "actor": "player:0", "item_id": 4}`
   - 使用药品：`{"type": "use_item", "actor": "player:0", "item_id": 17, "party_slot": 1}`
6. **全队背包与队伍真值**：
   - 队伍状态：`GET /api/v1/game/party`（直接解码 `0x0223B570 -> +0x194`）
   - 背包库存：`GET /api/v1/game/inventory`（直接解码 `0x0223B570 -> +0x190`）
7. **大地图寻路与草丛巡逻**：
   - 坐标导航：`POST /api/v1/navigation/tasks`
   - 草丛遇敌巡逻：`POST /api/v1/encounters/tasks`


---

## 五、 控制台实时 HUD 零抖动防抽搐工程准则 (Zero-Jitter Console HUD Guidelines)

本项目所有为人类开发者与测试员编写的实时交互脚本（如 `watch_radar.ps1`、`radar_compare.ps1` 等），必须严格遵守以下防抖动、防抽搐规范：

1. **严格单屏视口行数契合 (Strict Single-Window Fitting)**：
   - 绝不允许高频刷新的输出总行数超过控制台窗口的可见高度（`WindowSize.Height`）；
   - 一旦输出行数大于可见窗口高度，每次 `SetCursorPosition(0, 0)` 将光标移至顶部，而紧接着的 `Write()` 写入末行时，Windows 控制台内核必定强制将视口向下滚动；在下一次循环时视口又被强行拉回顶部，导致控制台在顶部与底部之间以 100ms 的频率剧烈来回反弹（即“上下一抽一抽”）；
   - **紧凑单屏设计**：双层切片等核心监控界面的总行数必须严格控制在 18~22 行以内，天然适应 Windows 默认 30 行的终端窗口，彻底杜绝视口滚动；
   - 若必须展示 31×31 大图，脚本必须在初始化阶段自动将 `$Host.UI.RawUI.WindowSize.Height` 扩大到容纳高度，若无法扩大则提供独立单屏展示模式。

2. **底行严禁输出尾随换行符 (No Trailing Newline on Bottom-most Line)**：
   - 多行字符串拼接后（`$outLines -join "`n"`），最后一行末尾绝对不能带有换行符 `
`；
   - 若光标已处于输出文本的最后一行末尾，附加的换行符会触发 Windows 控制台内核自动向上滚动整屏 1 行，造成规律性的微抖动。

3. **消除 `Clear-Host` 闪烁，使用原位双缓冲覆盖 (In-Place Double-Buffering Overwrite)**：
   - 严禁在主循环内部调用 `Clear-Host` 或 `cls`（会导致整屏瞬间黑屏重绘闪烁）；
   - 每一行文本严格按控制台宽度截断并用空格右填充（`PadRight`），静默覆写前一帧的遗留字符；
   - 单帧全部行组装完毕后，仅通过一次 `[Console]::SetCursorPosition(0, 0)` 归位并单次 `[Console]::Write()` 完整输出。

4. **PowerShell 脚本 UTF-8 BOM 编码铁律 (Strict UTF-8 with Single BOM)**：
   - 凡包含中文提示符、图例或 ANSI 字符的 `.ps1` 脚本，必须以单个标准 UTF-8 BOM（`0xEF 0xBB 0xBF`）保存；
   - 严禁缺少 BOM（会导致 Windows PowerShell 5.1 按系统本地 ANSI/GBK 误读而发生语法解析断裂），也严禁写入多重连续 BOM。


---

## 六、 模拟器到服务延迟与刷新节拍准则 (Latency Budget & Target Cadence)

所有实时监控脚本的 `IntervalMs` 表示“目标帧周期”，不是每次 HTTP 请求完成后的额外睡眠时间：

1. 每帧必须记录 `frameStarted`，请求/渲染完成后只睡眠 `max(0, IntervalMs - elapsedMs)`，禁止“请求耗时 + IntervalMs”导致周期叠加。
2. Emulator RPC 必须 single-flight：同一时刻只允许一个内存批量请求；相同模拟器帧/短时间窗内的重复读取必须复用缓存，禁止让浏览器轮询堆积在 Lua frame loop 后面。
3. 静态 ROM 地形、NPC、家具、Warp 与 Doorstep 坐标必须使用进程级 LRU/有界缓存；粗略雷达优先使用 `preview_surface_at`，详细/寻路验证才使用完整 `surface_at`。
4. 任何高频接口都必须避免每格重复扫描整张实体表；应使用 zone/tile 索引或 bounds 批量索引。
5. 延迟验证至少分别测量：HTTP 往返、PlayerRuntime/RAM 采样、ActorSystem 采样、静态雷达渲染；不能只看脚本表面刷新时间。


## 七、 独木桥/窄桥与训练家视野显示准则

1. ROM `TileClass 0xBE` 为独木桥主体，`0xBF` 为独木桥入口/出口；雷达必须与普通道路区分，主体使用 `╫`，入口使用 `╪`。
2. 独木桥按相邻 Catwalk 地块推断桥轴；桥轴方向可通行，垂直两侧标记为坠落边缘并加入 `blocked_exits`，不得把独木桥当普通平地。
3. 独木桥有 SWAN 已知的 `CatwalkBalance`、`CatwalkExit`、`Fall` 运行时状态。未完成 RAM 差分前，不得猜测具体停留阈值；必须暴露 `dwell_seconds`、当前状态和 `threshold_seconds=null`。
4. 训练家视野来自 ROM `sight_raw` 与当前 Actor/朝向；只要训练家未击败，视线射线应持续显示，不能因一次 ActorSystem 采样暂时缺失而闪烁消失。射线的每格方向符号必须保持 `^/v/</>`。


## 八、 真机数据唯一权威与测试数据隔离规范 (Live Truth Only)

1. **禁止把测试值、历史探针、示例值、截图人工标注或硬编码剧情进度写入 AI 当前上下文**。AI 当前上下文只能来自当前模拟器会话的 BizHawk Main RAM、当前 ROM 静态资源，或明确标注为“历史审计证据”的数据。
2. `runtime/ai_context/*.json`、`*.ndjson`、`runtime/navigation/observed_graph.json` 等文件只允许作为审计/历史证据，不能直接覆盖当前 Zone、坐标、队伍、背包、徽章、剧情旗标、NPC 胜负状态或战斗身份。
3. 所有实时事实必须携带来源与新鲜度：`source=live Main RAM` / `source=ROM` / `source=historical audit`，并保留 `frame` 或 `sampled_at`。缺少实时证据时必须返回 `unresolved`，不能返回空值并解释成“没有”。
4. 原始 EventWork 旗标可以暴露为 `raw bit ID`，但没有成对 ROM/RAM 差分证明时不得给旗标编造任务名称或剧情语义。
5. ROM 中的 Trainer `sight_raw` 只能表示 `candidate_rom_sight_only`；只有当前 Actor、训练家脚本、实时 TrainerFlag 与战斗转场证据闭合后，才能升级为已验证训练家状态。
6. 所有 API、前端、AI prompt 与自动化决策都必须优先使用当前 RAM 派生的 live projection；历史记忆只能以单独的审计字段出现，并明确 `excluded_from_authoritative_context=true`。
