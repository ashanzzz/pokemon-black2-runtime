# Pokémon Black 2 动态世界运行时逆向：项目级 Agent 约定

本文件是本项目所有后续 Agent 必须遵守的长期工作约定。它将用户提供的《Pokémon Black 2 Dynamic World Runtime Reverse Engineering Agent》任务书固化为项目规则；如与用户在当前对话中的明确指令冲突，以用户的明确指令为准。

## 使命与事实来源

本项目要构建基于 BizHawk 的 Pokémon Black 2 **动态运行时解析系统**，而不是攻略、记忆地图或截图推测工具。最终目标是从真实的游戏 RAM、当前加载的游戏资源及 ROM 静态资源中，实时恢复当前世界，输出可验证、带来源信息的结构化 Semantic API。

唯一事实来源：

```text
当前游戏 RAM + 当前加载的游戏资源 + ROM 静态资源 = 游戏事实
```

- 一切动态状态必须来自**当前 RAM**：玩家、地图/Zone、NPC 当前坐标与朝向、事件、触发器、动态对象、战斗、菜单、对话、相机、已加载 Chunk 等。
- 静态结构可以来自 ROM：模型、Matrix、Chunk、碰撞、Warp、NPC Spawn、Trigger、门、地形、纹理、脚本等；但必须由当前 RAM 确认当前正在使用的是哪一份资源。
- 不确定的字段必须输出 `unknown` 或 `unresolved`，绝不猜测或补全。

## 严格禁止

- 不得凭模型记忆、攻略网站、截图或已走过的路径生成/补全当前地图、道路、建筑、NPC、Warp 或边界。
- 不得以累计坐标取代真实游戏坐标，不得把历史状态伪装成当前状态。
- 不得把静态 Spawn Position 当成 Runtime Actor 的 Current Position。
- 不得为了 UI 好看而硬编码、猜测或补画数据；Renderer 只能消费 Parser 已确认的数据。
- 不得未经当前 ROM 实测就把 SWAN 地址直接写进正式 Runtime API。
- 不得修改 RAM 来制造“正确”结果；按键后也不得假定移动成功，必须重新观察并验证位置/状态。
- 不要过早投入最终 UI；先保证数据正确，可先做简单 Runtime Observer。

## 分层架构

```text
Pokémon Black 2 → BizHawk → Lua Bridge → Raw Memory API
→ Runtime Object Resolver → Pokémon Black 2 Parser → World Semantic API → AI / Renderer
```

正确的数据流必须是 `Game Data → Parser → API → Renderer`。底层 API 为 `memory.read`、`memory.read_batch`；中层为 `runtime.field`、`runtime.actor`、`runtime.map`；高层为 `world.snapshot`、`world.player`、`world.actors`、`world.map`、`world.collision`、`world.interactions`、`world.warps`。

API 应尽可能通过根指针和 Pointer Chain 解析，不依赖永恒固定的运行时地址。核心目标 Pointer Tree：

```text
GameSystem → Field → FieldPlayer → FieldPlayerCore → PlayerActor
```

Field 应进一步解析 Player、ActorSystem、G3DMapper、NoGridMapper、Camera、SceneArea、Terrain 等运行时系统。

## 逆向方法与验证标准

优先研究 `ds-pokemon-hacking/swan`，尤其 `system/gamesystem.h` 及 `field/` 下的 fieldmap、player、mmodel、position、terrain、eventdata、matrix、g3d mapper、camera、scenearea、rail、chunk、static prop 等定义和 `root.swandb`、`IREO.yml`。

- 在 `docs/swan_runtime_schema.md` 维护结构表：`Structure | Field | Offset | Type | Meaning | Runtime/Static | Verified`。
- `Verified` 只表示**当前 ROM 的真实实验**已验证；未验证内容标为 `SWAN hypothesis`。
- 每个字段使用离散置信度：`hypothesis`、`candidate`、`probable`、`verified`、`rejected`，不可只给一个百分比。
- 每个 API 字段必须能追溯来源：内存域、地址、基对象、偏移、帧号和验证状态。

采用主动、可复现的行为指纹，而不是仅凭“某内存值像坐标”。方向、位置和状态的候选地址须至少评估：

```text
idle_stability, direction_correlation, horizontal_correlation,
vertical_correlation, movement_correlation, repeatability,
return_to_origin, state_cardinality, SWAN_structure_match,
pointer_relationship
```

单地址匹配不足以确认结构。发现疑似 `GPos.X` 后，按 SWAN 偏移反推候选 Actor，并同时验证 FaceDir、MotionDir、GPos、WPos、ZoneID、ActorUID、CurrentTileUnder 等字段的整体一致性（Structure Coherence）。找到 PlayerActor 后从全 RAM 搜索其指针，逐级反向寻找 Core、Player、Field、GameSystem。

不要长期每帧全量扫描 4 MB RAM；流程应为：

```text
Discovery → Candidate Reduction → Focused Watch → Structure Validation → Pointer Search
```

## 当前优先级

按顺序推进，不得跳过验证：

1. Player FieldActor：`GPos`、`WPos`、`FaceDir`、`MotionDir`、`MovementFlags`、`CurrentTileUnder`。
2. Player Pointer Chain：`FieldPlayerCore`、`FieldPlayer`、`Field`、`GameSystem`。
3. ActorSystem：`ActorCount`、`ActorHeap`、actor stride、Runtime NPC。
4. Map Identity：`ZoneID`、`MatrixID`、`MapMtxSys`。
5. Map Chunk：`G3DMapper`、Chunk ID、已加载 Chunk、Chunk Position。
6. Terrain/Collision：TileType、Height、Slope、Walkability。
7. Warp/Trigger：entrance、exit、door、warp、trigger area。
8. Interactive Objects：NPC、sign、door、item、switch、script object。
9. Camera、Rail 与特殊地图系统。
10. 完整 World Semantic API。

## 第一个实验阶段：必须的动作序列

在开放区域执行以下实验；每一步记录 requested input、frame、before snapshot、during snapshots、after snapshot：

```text
IDLE
UP → RIGHT → DOWN → LEFT → UP
RIGHT × 2 → DOWN × 2 → LEFT × 2 → UP × 2
RIGHT × 4 → LEFT × 4
DOWN × 4 → UP × 4
RIGHT → DOWN → RIGHT → DOWN → LEFT → UP → LEFT → UP
IDLE → RIGHT × 1 → IDLE → RIGHT × 1 → IDLE
```

另应在可控条件下进行：长按移动（区分每 Tile 才变的 GPos 与逐帧连续变化的 WPos）、原地转向、walk/stop、walk/run、地图切换、Warp、Actor 生命周期、不同地形和碰撞实验。不要预设哪个轴是 X/Y/Z，应让观测结果决定。

必须建立/维护 `tools/runtime_memory_discovery.py`，至少支持 snapshot、memory_diff、timeline_record、input_sequence、candidate_filter、candidate_rank、structure_scan、pointer_search、report_generation。

## 运行时世界建模规则

- Runtime NPC 必须来自 ActorHeap；API 可同时提供 `spawn_position` 和 `current_position`，但必须严格区分。
- 地图恢复路径为：Current Zone → Map Matrix → Chunk IDs → 当前 Chunk 资源 → geometry；最终应由 Matrix、Chunk、Terrain、Collision、Static Props、Runtime Actors、Warps、Triggers 重建世界，不取决于玩家是否走过该区域。
- `FieldG3DMapper` 应用于确认玩家当前 Chunk 与加载的 Chunk；`MapTerrainBuf` 用于 SlopeX、HeightDiv、SlopeZ、TileType、HeightY；碰撞 bit 未知时命名 `unknown_bit_X`。
- Warp 至少恢复 id、源位置/Zone、目的 Zone/Warp、方向、转换类型、宽高。Trigger 至少恢复 SCRID 和 bounds。可交互性优先依据 Actor、SCRID、EvType、Trigger、Script，不得根据外观猜测。
- 相机应来自 `FieldCamera`/`G3DCamera`；非普通 Grid 地图要研究 `FieldRailSystem`，不得强行解释为二维 Tile Grid。

`world.snapshot` 应覆盖 frame、scene、map（zone/matrix/尺寸/current chunk/loaded chunks）、player（actor id、grid/world position、facing、movement）、actors、terrain、collision、warps、triggers、interactions、camera。

## 证据、报告与 Human-in-the-Loop

所有实验原始证据保存在 `reverse_engineering/`，建议结构：

```text
reverse_engineering/
  schema/{swan_schema.json, verified_schema.json}
  experiments/EXP_###_*/{metadata.json, timeline.csv, candidates.json, report.md}
  reports/
```

每完成一个重要阶段必须输出并落盘 `TEST REPORT`，包含：Goal、Hypothesis、Method、Actions performed、Memory ranges、Candidate addresses、Raw observations、SWAN correspondence、支持与反对证据、Confidence、Verified fields、Unresolved fields、Files changed、Next recommended experiment。

当前阶段工作流固定为：Agent 提假设 → 编写 Probe → 给出具体测试步骤 → 用户在真实游戏中测试 → Agent 收集数据 → 输出结果 → **停止**，等待用户决定下一阶段。不能在未获得真实测试数据时连续堆叠大量假设。

第一次阶段结束时必须输出 `RUNTIME REVERSE ENGINEERING REPORT`，至少包含 ROM information、Memory Domains、Experiment sequence、Frames captured、Changed/Filtered address counts、Top direction/coordinate/movement/SWAN-structure candidates、Best FieldActor candidate、pointer chain、confidence、evidence、failed hypotheses、unknowns、文件变更和向用户请求的精确下一测试；然后停止。

## 缓存、交互与用户输入

允许长期缓存 ROM resource parser、Chunk geometry、texture metadata、map matrix definitions、structure layouts 等静态资源结果。绝不可把 NPC 当前位置、当前 actor 列表、玩家位置、active trigger/script、map instance 等动态状态作为当前事实缓存。

所有游戏操作遵循：`Observe → Decide → Act → Observe → Verify`。

用户可能使用语音输入。仅当上下文明确支持、原词和目标词确有合理同音/近音关系时，才可纠正明显语音识别错误；无法确定则保留原文。不得擅自改动技术名词、地址、数值、hash、文件名或代码，更不得改动人名、地名、游戏/项目/GitHub 仓库/软件/机构/事件等客观实体。技术测试结果优先保留原始数据。

## 最终原则

我们不是在让 AI 记住 Pokémon Black 2；我们在建立实时游戏运行时观察系统：

```text
Game Memory + Game Resources → Runtime Parser → World Model → Semantic API → AI
```

地图、人物、NPC、位置、碰撞、入口、出口、Warp、Trigger、交互对象和渲染必须尽可能从游戏自身真实数据恢复。尚未解析出来的内容保持未知，继续逆向，不要猜。
