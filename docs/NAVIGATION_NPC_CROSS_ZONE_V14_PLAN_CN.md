# v14 导航、跨 Zone 与 NPC 可视化实施计划

## 已确认的当前状态

### 导航

- v13 已支持 `gen5-matrix-grid-v1`，同一个 Matrix 的 Zone 已可以由 ROM ownership 解析并用 lazy A* 连接。
- `Zone` 目前仍作为运行时归属和日志元数据；`Matrix + GPos(x,y,z)` 才是同 Matrix 的空间地址。
- 不同 Matrix 仍由 `NAV_MATRIX_TRANSITION_UNVERIFIED` 拦截，这是必要的安全边界，因为室内地图经常复用相同局部坐标。
- 单元测试已经覆盖同 Matrix 跨 Zone 解析、全局 A*、Zone-less API 和 fake executor。
- 实机历史记录证明了短距离同 Zone 执行和 Matrix 规划，但最长的一次实机跨 Zone 执行在第一段后因 `locomotion_not_idle` 停止，尚未形成“跨过 Zone 边界并在目标 Zone 到达”的证据。

### 坐标与拼接

- 3D 场景、玩家、NPC 和导航已经以 Gen5 canonical world/grid 作为事实坐标。
- `connected` 开关目前改变的是加载哪些同 Matrix 邻接 Zone；它不应再改变对象坐标。
- 不能把不同 Matrix 的室内、通道和道馆凭视觉相邻关系直接平移到同一平面；必须经过 Warp 入口、输入因果、切图前后 Zone 和落点的实机证据。

### NPC

- `/api/v1/map/v6/actors/live` 当前能读到本场 ActorSystem 活跃槽位；最近实机快照为声明 63、有效槽 9，其中 8 个非玩家实体加 1 个玩家。
- `/api/v1/ai/npcs` 当前只返回 ROM 静态 NPC；它没有把运行时 actor 与静态记录合并。
- 3D `applyActors()` 当前只画运行时 actor，`static.entities.npcs` 没有对应的 3D overlay。因此当前只能看见已解码的运行时实体，不能看见所有 ROM NPC 候选。
- 静态 NPC 的坐标轴已经明确为 `event.x -> grid.x`、`event.y -> grid.z`、`event.z -> grid.y`；不能使用未经转换的事件坐标直接绘制。

### 性能与内存

- `OriginalWorldService.zone()`、`ExportedWorldStore.zone()` 以 `lru_cache(maxsize=512)` 缓存完整 Zone bundle。
- connected scene 会合并多个 Zone 的完整 terrain/building/entities 结构；读取大 cluster 后观测到 Python 私有内存约 6.9 GB，随后 HTTP 进程退出。
- 这说明 v14 必须限制 cluster 的返回体和缓存对象生命周期，不能在后台轮询中重复构造完整大对象。

## 设计结论

### 1. 一个坐标系，两个视图范围

保留唯一事实坐标：

```text
canonical Grid: gen5-field-grid-v1 / gen5-matrix-grid-v1
horizontal axes: GPos.x, GPos.z
height layer: GPos.y
world center: WPos.x = GPos.x * 16 + 8
              WPos.z = GPos.z * 16 + 8
```

默认视图加载当前 Zone 所在的同 Matrix、同室外、cardinally adjacent component。关闭“拼接相邻室外”时，只减少渲染范围，不生成另一套局部坐标，也不改变导航目标、NPC 坐标或路线坐标。

室内和不同 Matrix 保持独立 scene domain。未来跨 Matrix 导航使用 `WorldConnectorGraph`，每条边必须携带入口站位、触发输入、切图等待、目标 Zone、落点和实机证据。

### 2. NPC 采用双层合成模型

对每个当前 Zone 或 connected cluster 的 NPC 输出一个统一行：

- `source.static_rom`: ROM spawn 定义、脚本、flag、原始坐标和候选分类；
- `source.runtime_actor`: 当前 ActorSystem 位置、朝向、UID、frame；
- `presence`: `runtime_present | rom_candidate | unresolved`；
- `binding`: `verified | probable | candidate | unresolved`；
- `render_mode`: `live_actor | static_candidate | unresolved_marker`。

绑定优先使用明确 identity，其次使用 script/flag/model 的组合，最后才使用同层坐标候选。运行时 actor 覆盖静态候选的位置，但不得因为没有匹配 actor 就宣称 NPC 已消失。

3D 默认绘制当前 connected component 内的全部静态 NPC 候选；实时 actor 用同一 ID 合并并替换位置。UI 用颜色、标签和透明度区分实时与候选，避免把 ROM 候选误当作当前碰撞事实。全 ROM NPC 使用分页 catalog，不一次性加载到场景。

### 3. 跨 Zone 执行必须先修复闭环

执行器需要把“输入队列已清空”和“角色已经 Idle”分成两个条件：

1. 到达 segment endpoint 时立即 clear input，防止 overshoot；
2. 在有限 settle window 内继续采样，允许 `Moving/Brake` 正常回到 `Idle`；
3. 只有 settle window 超时、位置未到达、切图或 screen 非 Field 时才失败；
4. Zone 改变但 Matrix 和 `(x,y,z)` 连续时，记录 `zone_transition`，继续执行；
5. Matrix 改变时暂停并要求 connector state machine 接管。

跨 Zone live 测试必须用一条经过真实 ownership 边界的短路线，先验证 1 次边界跨越，再验证 2 个方向和一个远距离路线；每一步记录 frame、Zone、Matrix、GPos、WPos、phase、clear 时间和最终 arrival。

### 4. AI 视野不是全局地图：采用“观察、记忆、推断”三层知识模型

不能把 ROM 静态地图或已解码的全 Matrix 直接冒充为 NDS 当前画面。这样虽然方便寻路，却会让 AI 获得玩家不可能知道的障碍、NPC、事件旗标和门后落点；同时，单次 API 输出又会让 AI 像没有记忆一样遗忘已经走过的道路。

最优折中是让每一条地图事实都带上来源和认识状态：

- `observed`：由带来源、frame、玩家实测位置的 runtime 或 capture 记录确认的事实；它不自动等同于“屏幕中可见”。只有截图、同帧状态和相机/遮挡校验齐备后才可升级为 `screen_verified`。
- `remembered`：过去的 `observed` 聚合而成，保留首次/最后观察帧、置信度、动态信息 TTL 和地图/存档上下文。
- `static_candidate`：ROM 能解出的静态地形、Warp、NPC spawn；可用于离线规划，但必须标记为候选，不能谎称已经亲眼看见。
- `predicted`：根据静态 ROM、当前朝向、计划路线投影出的未来局部视野；仅供计划比较，真实走到该处后必须被 `observed` 覆盖。
- `unknown`：没有可靠观察或静态证据。API 不得自动用全局 ROM 数据填充它。

`observed > remembered > static_candidate > predicted > unknown` 只表示展示和决策优先级，不表示 ROM 候选会变成实机事实。

### 5. 统一的是空间地址，不是把所有 Matrix 强行放到一张平面

全局唯一键应为：

```text
SpatialAddress v2 = {
  rom_sha256,              // 实现前为 null + identity_status=unresolved，不能伪造
  matrix_id,
  grid: { x, y, z },
}

FieldAddress v2 = SpatialAddress + {
  zone_id,                 // ownership / runtime scene metadata, not spatial identity
  zone_resolution
}

WPos.x = GPos.x * 16 + 8
WPos.z = GPos.z * 16 + 8
```

其中 `matrix_id` 是地址的一部分。相同 Matrix 内的 `GPos`/`WPos` 是连续事实坐标；切换 connected/unconnected 只改变返回和渲染范围，永远不改变它。不同 Matrix 可能重用同一 `(x,y,z)`，因此它们只能在拓扑 `WorldConnectorGraph` 中通过 portal 边相连，不能因数值或画面接近而变成一个连续欧氏平面。

浏览器地图应显示“同 Matrix 拼接地表”与“跨 Matrix portal 箭头”两种关系。这样对 AI 来说仍是一个统一世界模型，而路径长度不会伪装成穿墙直线距离。

## 目标 API 契约（Phase E）

### 当前真实视野：`GET /api/v1/ai/view/current`

默认使用严格 `strict` 模式：返回 runtime frame、capture reference 及其明确的
alignment 状态，不把当前 bridge 尚未证明同帧的截图和 RAM 说成原子快照，也不把
ROM 邻近地块称为已见。显式 `assisted_local` 才提供 9x7 的
`nds_tile_window_v1`（默认 `radius_x=4`、`radius_z=3`），且全部保留为
`static_candidate`。完成相机投影和遮挡实测后才升级为
`screen_verified` / `nds_camera_frustum_v2`；未验证 profile 必须明确降级，不能
伪造屏幕可见性。

截图采集必须是显式的异步工作流，而不是在 GET 中偷偷触发：

```text
POST /api/v1/ai/view/captures             -> 202 + capture_id
GET  /api/v1/ai/view/captures/{capture_id}
GET  /api/v1/ai/view/current?profile=...
```

未来的 `atomic_captured` 只能来自新的受限 bridge 操作：同一 Lua tick 读取已验证
Player/Mapper/ActorSystem 的小范围 RAM、保存截图并返回 frame。不得拿
`memory.dump_universal` 的全内存 dump 作为日常视野采样，也不得靠普通
`screen.capture` 和之后的 RAM GET 拼出“同帧”事实。

每个响应必须含：

```json
{
  "format": "black2-ai-current-view/v1",
  "frame": 123456,
  "observation_context": {"scene_generation": 87},
  "address": {"matrix_id": 0, "grid": {"x": 145, "y": 2, "z": 645}},
  "profile": {"requested": "strict", "effective": "runtime_snapshot_v1"},
  "capture_alignment": {"status": "not_frame_verified"},
  "visibility": {"mode": "screen_unparsed", "occlusion": "unresolved"},
  "tiles": [],
  "entities": {"runtime_actors": [], "static_candidates": [], "warps": []},
  "provenance": {"player": "runtime_ram", "terrain": "rom_projection", "actor": "runtime_actor_system"}
}
```

`tiles[]` 中每格都需要 `address`、terrain/height、`walkability`、`visibility_state`、`knowledge_state`、占位来源和 warp 候选。`assisted_local` 的 ROM 格子固定为 `static_candidate`；只有同时有当前屏幕/相机/遮挡证据的格子可标为 `observed`。遮挡模型未验证前，`screen_visible` 必须是 `unresolved`，而不是 `true`。

真正的 NDS 画面等价 API 分两步完成：先把当前窗口与玩家/相机 frame 精确绑定；再以顶屏截图、已验证相机矩阵、3D 深度/遮挡射线交叉验证。只有二者一致的 tile/entity 才升级为 `screen_visible: verified`。这避免把“距离玩家约 50 格”误报成“肉眼已经看见”。

### 全局视野：`GET /api/v1/ai/view/global`

它是索引和知识图，而不是一次性返回 615 个 Zone 的完整几何、GLB 或 NPC。参数应至少包括：

- `scope=matrix|visited|route|catalog`；默认 `visited`。
- `knowledge=observed|memory|hybrid|rom_candidate`；默认 `memory`，`rom_candidate` 需要显式请求。
- `matrix_id`、`bounds` 或 `cursor/limit`；强制分页和最大 tile/edge 数。
- `lod=zone|cell|tile`；默认 `zone`。
- `session_id`；用于隔离不同 AI/存档的记忆。

响应必须分开 `observed_coverage`、`memory_coverage`、`static_catalog_coverage` 和 `unknown_coverage`。全局拓扑可展示 verified connector；未经证据的 Warp 只能作为 `candidate_connector`，不能作为可执行路线边。

### 记忆与自动行走：`/api/v1/ai/memory/*`

采用事件溯源的持久记忆服务，而不是把每帧完整 scene 存进 RAM：

```text
MemoryNamespace = rom_sha256 + continuity_id + memory_epoch
ObservationFrame (append-only, 有界保留)
VisitedTileMemory (按 FieldAddress 聚合)
LandmarkMemory (NPC / Warp / 标志物，动态字段有 TTL)
RouteEpisode (实际按键、采样点、失败或到达结果)
TransitionEvidence (Warp 前后 frame、输入、Zone/Matrix/落点)
```

实现上使用单一 SQLite/WAL 数据库或已有项目持久存储目录中的等价事务存储；写入经一个异步队列批量提交。原始观察帧只保留有限滚动窗口，tile/landmark 聚合记录保留可审计的 first/last seen、证据帧、hash、置信度。禁止把未验证 NPC/flag 当作永久记忆；动态 actor 在短 TTL 后降级为 stale，静态 terrain 在 ROM hash 相同且 scene 版本相同时可长期复用。

`GET` 保持只读，且不得触发 bridge 读取、截图、全量 connector、connected scene 或 GLB 解码。导航执行器在每个已验证 tile、Zone transition、Warp loading 边界主动写入 observation；需要人工或外部 agent 写入时使用显式 `POST /api/v1/ai/memory/observations`。`continuity_id` 优先使用将来可验证的 save identity；在它尚不存在时，bridge session 只能作为 observation provenance，不能当作记忆主键，遇到 reset/load-state、frame 倒退或无法解释的 reconnect 必须新建 `memory_epoch`，绝不把旧世界的“记忆”静默带入新进度。

### 计划视野模拟：`POST /api/v1/ai/view/predict`

输入是已创建的 navigation plan 或有 provenance 的 global path，输出最多受控 `horizon_steps` 个未来局部窗口。它沿每个 FieldAddress 和预测朝向投影同一个 `profile`，并把每项固定标为：

```text
mode: predicted
source: static_rom + remembered_observations + plan
confidence: candidate
```

模拟不得生成“已观察”记录，也不得假设 NPC 仍在原地、脚本未改变或门后的 Matrix 落点已知。路径遇到未验证 connector 时应停止并返回 `NAV_TRANSITION_UNVERIFIED`；遇到 verified connector 只可生成 `loading/unknown` 过渡帧，直到真实 runtime sample 确认落点。

## 扩展实施阶段

### Phase E：Current View、Global Knowledge 和 Memory

1. 新增共享 `FieldAddress`/`KnowledgeState` schema，供 scene、导航、NPC、观察和记忆共用；禁止各 API 自行拼坐标。
2. 先实现纯只读 strict current-view builder；再实现显式 assisted_local 投影，复用已验证 player snapshot、matrix ownership、static terrain 与 runtime actor overlay；为每一个字段附 `source` 和 `confidence`。
3. 实现 namespace 隔离的 memory repository、显式 session lifecycle、observation aggregation 和 navigation hook；为 RAM 不可用、旧 frame、存档切换、TTL 过期写测试。
4. 再实现分页 global index；默认只显示记忆，明确请求后才叠加 ROM catalog。响应设硬上限，不能触发 connected scene / GLB 解码。
5. 最后实现预测视野。它必须复用同一个 local-view projection、限制 horizon，并有 `predicted -> observed` 覆盖测试。
6. 仅在截屏与相机/深度数据有足够实机证据后实现 screen_verified profile；在此之前 strict profile 是诚实、稳定的第一版。

### Phase F：验证跨 Matrix connector

1. 每个候选门至少采集两次独立、方向一致的真实转场证据，包含入口 FieldAddress、触发动作、loading frame、目标 FieldAddress 和落点稳定帧。
2. evidence promotion 生成不可变 `connector_id`、证据引用和版本；只有 `verified` edge 可进入 Composite Task。
3. Composite Task 执行 `approach -> trigger -> loading -> settle -> observe -> continue`，每一状态有超时、撤销输入和可审计事件。
4. 到达新 Matrix 后先写当前真实视野，再继续后续路径；若任何 precondition 不成立，安全停止并保留最后验证地址。

## 实施阶段

### Phase A：坐标和视图契约

1. 将 connected/unconnected 的 API、scene、导航和前端点击路径统一到 canonical grid/world。
2. 增加断言：关闭拼接不能改变任意实体的 canonical 坐标；同一实体在两种视图的 `id + world/grid` 必须一致。
3. connected scene 改为 bounded payload，只返回视口需要的 terrain/building/entity 索引；大对象通过已有 asset URL 懒加载。

### Phase B：跨 Zone 执行器

1. 修正 segment endpoint 的 settle 状态机。
2. 增加 Matrix domain 检查和 `zone_transitions[]` 事件。
3. 为跨 Zone 任务保留原始 global path，不因 Zone 改变清空前端路线。
4. 失败时保留 `last_verified_node`、`last_zone_id`、`last_matrix_id` 和完整 landing diagnostics。

### Phase C：NPC 合并和显示

1. 后端增加统一 NPC scene payload，合并静态 ROM 与 live ActorSystem。
2. 3D 增加静态 candidate marker，运行时 actor 覆盖位置和朝向。
3. 在 Explorer/Inspector/Navigation occupancy 中区分 candidate 与 runtime；只有 runtime actor 才作为硬占位约束。
4. connected scene 跨 Zone 汇总 NPC 时保留 `source_zone_id`，不把各 Zone 的局部事件坐标混成未标注坐标。

### Phase D：Warp/跨 Matrix 状态机

1. 先收集真实 Warp 证据：入口 tile、按键、切图 frame、目标 Zone、目标 GPos、落点偏移。
2. 将两次以上一致证据提升为 verified connector。
3. 执行复合任务：`approach -> trigger -> loading -> resolve new scene -> continue`。
4. 未验证的 Warp 继续返回明确的 `NAV_TRANSITION_UNVERIFIED`，不猜 landing。

## 验证矩阵

### 离线

- 全部单元测试；live-only EXP-021 与服务器是否运行解耦，不能把当前对话状态误报为代码回归。
- API contract：global resolve/snap、connected scene、NPC merged payload。
- 坐标不变性：connected 与 unconnected 同一 Zone 的 entity 坐标完全相同。
- NPC 合并：静态全量、runtime 覆盖、未匹配候选、玩家排除、不同高度层。
- executor settle：endpoint clear 后 Moving->Idle、Zone transition、Matrix transition、overshoot 和 stuck。

### 实机

- 当前 Zone 内 3 步短路线。
- 同 Matrix 相邻 Zone 的边界前后各 1 格路线。
- 同 Matrix 跨 Zone 远距离路线，要求 `zone_transitions >= 1`、无 `NAV_POSITION_DIVERGED`、最终 GPos 与 global target 一致。
- 反向返回路线。
- 进入门/独立 Matrix：先验证失败码正确，再用已采集 connector 证据测试复合任务。
- 打开/关闭拼接地图，检查 NPC、玩家、terrain、路线坐标没有漂移。

### 性能

- 每次 scene 请求记录 payload bytes、terrain/building/NPC 数、Python RSS 和请求耗时。
- 连续刷新 100 次 connected scene，RSS 必须稳定，不允许随 Zone 数线性累积。
- 默认场景只加载 bounded component；完整 ROM NPC catalog 必须分页。

## Terra 实现边界

Terra 只修改 Phase A-C 所需的后端、前端和测试文件，不改 ROM 原始数据、不删除用户现有未提交改动、不放开未经证据验证的跨 Matrix connector。完成后由主代理审核 diff、逐项运行验证矩阵，并根据实机结果决定是否进入 Phase D。

## 2026-09-08 审查补充：必须先消除的阻断项

在开始 Phase E 前，以下问题必须有自动化回归测试。它们会使界面或观测层把
“同 Matrix 拼接”错误地降级为不同世界，或者把相邻 Zone 的点击送到错误的
Zone。

1. 浏览器收到 Player Zone 改变时，不能立刻删除路线。它应等待新 scene 的
   Matrix identity；同 Matrix 保持路线和目标，不同或未能确认 Matrix 才停止并
   标记为 stale。
2. 拼接场景中，terrain、静态 NPC、warp、door 的点击坐标必须继承命中的
   `source_zone_id` 和已知 `matrix_id`。不得用 scene anchor Zone 覆盖它。
3. 已知当前 Matrix 时，没有 Matrix provenance 的路线点、目标和点击点不得
   被渲染或执行为“当前 Matrix 内”。未知是停止条件，不是兼容性通配符。
4. `ObservedNavigationGraph` 必须能记录同 Matrix、相邻 GPos 的 Zone boundary
   证据；目前若先要求 Zone 相同，`zone_transition` 分支不可达。跨 Matrix 的
   Zone 改变不得写入普通 tile graph，只能进入独立的 portal evidence 流。

这些修复是 Phase A-C 的验收门槛，不是未来优化。没有它们，就不能把一次
实机 Zone 改变称为已验证跨 Zone 执行。

## 最优的 AI 感知与记忆方案

### 结论

不要在 API 中二选一地提供“全局上帝视角”或“每次请求都失忆的局部格子”。
最合适的模型是：**当前感知、持久记忆、显式地图知识、受限预测** 四层并存，
任何格子或实体都附带来源、时间和置信度。

路径上的历史视野也不应当通过重新渲染整段 3D 场景来模拟。那会把每一步的
几十个格子、截图和动态角色重复存储，成本随路径长度快速增长，而且仍无法
证明真实相机遮挡。应在实际移动时记录稀疏 ObservationFrame；需要回放时，
从这些帧和聚合记忆按需重放“当时已知的事实”。这比保存全局视图或逐帧视觉
模拟更诚实、可审计且更省内存。

### 统一空间地址

`Zone` 不应再是坐标原点。建议新增一个共享且不可变的 schema，由导航、
3D、感知和记忆共同使用：

```text
SpatialAddress = {
  space: "gen5-matrix-grid-v1",
  rom_sha256,              // 未计算时必须声明 identity_status=unresolved
  matrix_id,
  grid: { x, y, z }
}

FieldAddress = SpatialAddress + {
  zone_id,                 // ROM ownership / current runtime scene metadata
  zone_resolution          // verified | rom_owned | unresolved
}
```

- `SpatialAddress` 是同 Matrix 地表连续坐标的真正身份；`zone_id` 在边界处是
  ownership/运行时场景元数据。
- 不同 Matrix 即使有相同 `(x,y,z)` 也不是同一地址，必须由 connector graph
  连接。
- `WPos` 始终是同一投影：`x = GPos.x * 16 + 8`、`z = GPos.z * 16 + 8`；视图
  是否拼接不能改变它。
- 地址未知时 API 返回 `null + unresolved reason`，禁止填写当前 Zone/Matrix
  作为猜测的默认值。

### 当前真实视野 API

首版不要声称“ROM 邻近格子就是屏幕看见的格子”。NDS 的真实可见性至少需要
截图、相机姿态和遮挡三者的一致证据。推荐把 API 分为两条同步但不同语义的
输出：

```text
GET /api/v1/ai/view/current?profile=strict&capture=latest
GET /api/v1/ai/view/current?profile=assisted_local&capture=latest
GET /api/v1/ai/view/current?profile=screen_verified&capture=latest
```

共同的 frame envelope 必须来自同一或明确标记的近似 frame。普通 `GET` 只读缓存；需要新截图时由显式 capture workflow 创建 artifact：

```json
{
  "format": "black2-ai-current-view/v1",
  "observation_id": "obs_...",
  "frame": 123456,
  "address": {"matrix_id": 0, "grid": {"x": 145, "y": 2, "z": 645}},
  "observation_context": {"scene_generation": 87},
  "mode": "screen_unparsed",
  "capture": {"status": "cached", "frame": null, "alignment": "not_frame_verified", "image_url": null, "sha256": null},
  "tiles": [],
  "entities": [],
  "provenance": {}
}
```

- `strict` 是第一版默认值：返回最近的原始 NDS screen capture reference、runtime
  PlayerRuntime、可验证 runtime actor 和文字/状态事实，并逐项标记 frame/alignment。
  当前 bridge 的普通 `screen.capture` 尚不能通过 transport 保留可核对的同帧证据，
  所以第一版不得声称截图与 RAM 原子同步。没有完成像素到格子的标定时，它也不输出
  假装“看见”的 ROM terrain tile。
- `assisted_local` 是显式可选的工程辅助：固定 9x7（63 格）或经过配置限制的
  局部 ROM 候选窗口，适合调试和安全规划；其中每格必须是
  `knowledge_state: static_candidate`，不能写作 `observed` 或
  `screen_visible: true`。这保留了用户所说的约 50 格局部范围，又不让 AI 静默
  获得全局上帝信息。
- `screen_verified` 只有在 bridge 的 `screen.capture` 截图、同帧 PlayerRuntime
  和经过实机校准的投影/遮挡模型一致时才输出 `screen_visible: verified`。未满足
  时必须降级到 `strict` 并说明原因，不能伪造精确视野。
- 截图不是每个 HTTP GET 都重新抓取。RuntimeHub 以有界频率保存最近 capture
  reference，或由显式 capture 请求更新；API 只返回引用、frame/alignment、哈希和
  新鲜度。当前 `screen.capture` 的 frame 必须由 bridge/transport 保留后才能标为
  已验证；这避免高频截图拖慢 BizHawk 或把图片塞进内存。
- 每个 tile/entity 必须分别标 `knowledge_state`、`source`、`observed_frame`、
  `dynamic_ttl`。动态 Actor 是本帧事实但很快过期；ROM terrain 仍只是静态候选。

### 记忆、路线回放和全局知识

持久层使用 SQLite/WAL（单一写入队列、批量提交），而不是把完整 scene 或图片
放进 Python 内存。数据库、WAL 和图片 artifact 放在可配置的本地 runtime 目录，
不能让同步盘承受高频 WAL 写入；数据库只保留图片 ID、hash、大小和保留期，不存
PNG BLOB。Namespace 为：

```text
rom_sha256 + continuity_id + memory_epoch
```

核心表/聚合为：

| 数据 | 写入频率 | 保留方式 | 用途 |
| --- | --- | --- | --- |
| `ObservationFrame` | 进入新格、Scene/实体显著变化 | 有界滚动窗口 | 可审计的第一人称原始证据 |
| `VisitedTileMemory` | ObservationFrame 聚合 | 按 FieldAddress 长期保留 | 首次/最后看见、可通行证据、覆盖次数 |
| `LandmarkMemory` | 发现 NPC/Warp/标志物 | 动态字段 TTL，静态字段带 ROM revision | 记住门、NPC 最后位置和不确定性 |
| `RouteEpisode` | 每个导航任务 | 动作段 + observation_id 引用 | 按需回放一段已走路径 |
| `TransitionEvidence` | loading 前后稳定帧 | 两次一致才升级 connector | Warp/门的执行证据 |

`bridge_session_id`、`scene_generation` 和原始 runtime frame 是每条 observation 的
provenance/context，不属于 `SpatialAddress` 或长期记忆 namespace。若要严格回答
“某个 AI 当时究竟被 API 告知过什么”，再启用可选的 `AgentDisclosureLedger`，记录
response hash、schema、agent/session 与 observation sequence；否则 replay 的准确名称是
“当时的世界观察证据”，不能把服务端记忆冒充成模型内部记忆。

因此“AI 沿路径看过什么”应使用：

```text
GET /api/v1/ai/memory/routes/{episode_id}/replay?from_step=&to_step=&view=known_at_time
```

它只返回该范围内的 ObservationFrame 引用和在当时已经成立的记忆快照，默认
限制步数/格子数。它不是假装重新看到旧画面，也不会把之后才探索到的信息倒灌到
过去。需要真实像素时，返回已保存且仍在 retention 内的 capture reference；没有
就明确返回不可用。

```text
GET /api/v1/ai/view/global?scope=visited&knowledge=memory&lod=zone
```

默认只返回 AI 已观察/记住的 coverage 和 verified connector。`knowledge=rom_candidate`
必须显式请求，并独立叠加为静态地图层；不能和 current view 混用，也不能一次性
返回 615 个 Zone 的 geometry 或所有 NPC。

### 预测不是记忆

`POST /api/v1/ai/view/predict` 只对已存在的 plan 做有限 horizon 投影，输出固定
`knowledge_state: predicted`、`confidence: candidate`、`source: plan + static_rom + memory`。
它不写入 `ObservationFrame`，也不能通过未验证 connector；到门时输出
`loading/unknown`。真实观察到目标位置后，observed 记录覆盖 prediction，而不是
反过来。

### Phase E 的安全交付顺序

1. FieldAddress schema、Matrix provenance 和上述 P1 交互/观测修复。
2. 无副作用的 strict current-view builder；再加入显式 `assisted_local`，所有
   字段带来源；用 fake Player/ROM/Actor 测试，不依赖 bridge。
3. RuntimeHub observation writer 和 SQLite 聚合，处理 session/reset/save epoch、
   TTL、队列背压和 retention。
4. 分页的 memory-first global index 与 RouteEpisode replay。
5. bounded prediction。只有完成屏幕/相机实机标定后，才打开 `screen_verified`。

每一阶段独立可测、可审核；Phase E 不会绕过当前的
`NAV_MATRIX_TRANSITION_UNVERIFIED`，也不会把 ROM 静态地图伪装成 AI 的当前感知。
