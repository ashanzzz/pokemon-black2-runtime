# Black 2 AI 语义 API v2

本文档定义让外部 AI 读取、理解并规划《口袋妖怪黑 2》运行时的稳定接口。实现位于 `backend/black2/api/semantic_routes.py`，统一前缀为 `/api/v1`。v2 增加有界地图窗口、地形材质目录、连接器解析和动作发现。

## 统一坐标和置信度

地图格坐标使用 `gen5-field-grid-v1`：

- `zone_id`：当前地图/区域编号。
- `x`：东西方向格。
- `y`：楼层或高度层（不是屏幕像素）。
- `z`：南北方向格。
- 一个格对应 16 个世界单位；世界坐标空间为 `gen5-field-world-v1`。

每条语义都应读取 `confidence` 或 `evidence`：

- `verified`：运行时直接观测或 ROM 结构精确解析。
- `probable`：独立证据一致，但尚未完成行为验证。
- `candidate`：保留原始证据的候选分类，可用于提示路径规划但不能当作事实。
- `unverified`：只有原始字节/编号，尚无游戏含义。

## 入口总览

`GET /api/v1/game/capabilities` 返回机器可读的入口清单和坐标契约。

`GET /api/v1/game/state` 返回帧号、画面状态、玩家运行时坐标、当前 Zone 和可控性。

`GET /api/v1/ai/context` 返回一个有界上下文文档，聚合 state、party、inventory、map、warps、doors、interactions、任务/目标、旗标和过场状态，适合每个决策周期直接提供给 AI。`scene_url` 指向完整场景投影；上下文中的门和交互仍携带各自的证据等级。

`GET /api/v1/game/actions` 自动列出输入、导航、对话动作的参数和副作用；`GET /api/v1/ai/materials` 返回 TileClass/Flags 材质词典。

## 地图和单格语义

`GET /api/v1/ai/map` 返回 Zone 矩阵、规则、事件、连接器和玩家附近窗口。`radius` 限制窗口大小（0-16），`include_raw=true` 返回原始地形记录，`text=true` 返回 ASCII 地图。

`GET /api/v1/ai/map/window?zone_id=&x=&y=&z=&radius=` 返回固定行主序窗口。每格包含地面层、材质、静态碰撞、相对高度和交互连接器；`projected_rows` 只用于展示，不能直接当作可执行网格。

`GET /api/v1/ai/map/tile?zone_id=&x=&y=&z=` 查询单个格。响应固定包含：

- `surfaces[]`：每层的材质、TileClass/Flags、高度采样和碰撞规则。
- `terrain` / `collision`：实时玩家锚定高度后才提升；多层歧义保持 unknown。
- `interactions`：该格命中的 Warp/入口/出口候选。
- `runtime_tile_type`：玩家当前脚下 TileType（只在当前格可用）。

TileClass/Flags 和 bit0 静态碰撞来自 ROM 结构；`can_walk` 仍可能是 null，因为 NPC、脚本、楼层和交通模式是动态条件。路径执行必须在动作后重新读取位置确认结果。

跨 Zone 查询会返回 `status=outside_loaded_zone`、`terrain.kind=unknown`、`collision.can_walk=null`，避免把另一张地图的数据误用到当前地图。

`GET /api/v1/ai/map/interactions` 或 `GET /api/v1/game/interactions` 将当前 Zone 的静态事件归一化为可供自动化决策使用的交互索引。可只传 `zone_id` 获取整张地图，也可传 `x`、`z`（`y` 可选）和 `radius` 获取坐标附近对象。每条记录包含：

- `kind`：`warp`、`npc`、`furniture` 或 `trigger`。
- `coordinate`：统一的 `gen5-field-grid-v1` 坐标；事件原始 `x/y/z` 的轴转换记录在 `axis_mapping`。
- `affordance`：建议的输入动作（例如面对 NPC 按 A、走上 Warp）；这是候选提示，不是执行授权。
- `availability.can_interact`：在运行时旗标、脚本、朝向和动态占用尚未解码前保持 `null`。
- `connector`/`destination`：Warp 的目标 Zone/Warp 和落点候选；真实切图前 `landing_status` 仍为 `not_observed`。

没有新鲜玩家坐标时，省略坐标参数不会伪造“附近”结果：响应的 `query.origin_source` 会说明是 `runtime_player`、`cached_player` 或 `none_stale_or_unresolved`。`ai/context` 在能解析当前地图时也内嵌同一份有限半径交互索引，便于 AI 每个决策周期一次读取。

## 入口、出口与跨地图图

`GET /api/v1/ai/map/portals` 读取当前场景的 warp/door 候选。每个记录含源地图和格、目标 `map_header_id`/`warp_id`、`destination.tile`（尚未观测时为 null）以及 `requires_transition_observation`。

`GET /api/v1/ai/map/warps?zone_id=&offset=&limit=` 返回 ROM 全部入口/出口，包含 `role`、源坐标、目标 Zone/Warp、候选落点、反向记录、`landing_status` 和 `traversal.can_traverse`。目标落点在实时切图前保持 `not_observed`。

`GET /api/v1/ai/map/zone/{zone_id}` 返回单个 Zone 节点及其连接器。

`GET /api/v1/ai/map/scene?zone_id=&radius=&include_raw=` 返回供地图 UI 或规划器使用的完整规范化场景图。顶层和 `normalized` 均提供 `portals`、`doors`、`actors`、`geometry`、`collision`：

- `portals/warps`：源 Warp、目标 Zone/Warp、反向候选和未验证的落点。
- `doors`：DoorUID、建筑旋转后的世界坐标、门偏移、建筑 GLB 引用和最近 Warp 候选；当前 ROM 没有独立门 mesh 时 `independent_asset.available=false`。
- `actors`：ROM 静态 NPC 出生记录与可选运行时 ActorSystem 叠加分开返回，静态记录不能当作当前碰撞占用。
- `geometry`：矩阵区块、地形/建筑资源引用和 permission 原始候选；GLB 通过已有 map/v5 资源接口按需读取。
- `collision`：当前窗口的静态碰撞/高度信息和动态限制；`executable=false` 表示尚未用运行时边证实全部通行规则。

`GET /api/v1/map/v6/scene/zone/{zone_id}` 是工作台的只读静态预览入口。它保留原有 `terrains`、`buildings`、`entities` 字段，并额外返回 `static`、`scene_origin`、`player`、`navigation_preview` 和 `preview_only=true`。其中 `player.source=static_rom_preview` 只是浏览器的镜头锚点，不能当作实机玩家位置，也不会授权导航执行。`navigation_preview` 是离该画面锚点最近、且至少包含一条直接实机观测边的有向分量；其 `start` 可用于离线绘制，但仍是历史位置而不是当前玩家位置。Bridge 未连接时可在工作台工具栏输入 Zone，或使用 `#world?zone=439` 直接打开预览。

门没有独立 ROM 网格时，`doors[].render_hint.mode=semantic_overlay` 表示前端使用门框、门槛、地面环、方向标记和 DoorUID/Warp 标签来显示语义位置；`resource.independent_asset.available=false` 仍然保留，避免把 UI 标记误认为原始门材质。`warp_association` 只是最近候选，真正的入口/出口关系与通行状态要靠运行时切图证据升级。

该场景接口是只读投影，不会下载大模型或执行输入；跨 Zone 规划仍必须在实时切图后确认 landing tile 和 traversal。

`GET /api/v1/ai/semantics` 返回坐标空间、置信度和语义升级规则，供外部 AI 读取后避免误用候选值。

## 队伍和背包

`GET /api/v1/game/party` 与 `GET /api/v1/game/inventory` 已固定响应格式。若 `decode_status=unverified`，`slots/items=[]` 的含义是“尚未解码”，不是队伍或背包为空；AI 不得据此做资源决策。字段列表预先固定，完成 RAM 逆向后可无破坏地填充。

## 路径规划和执行

导航请求还可带 `movement_mode`：`auto`、`walk`、`run`、`bike` 或 `surf`。`auto` 默认选择安全的走路；`run` 不按“室内/室外”字符串硬编码，而读取 ROM `ZoneHeader.enable_running`，因此允许跑步的室内地图也能运行。`bike` 必须同时满足 ZoneHeader 允许骑车、当前 PlayerRuntime 已确认处于 `Cycling`，且路径没有标记为 `blocks_cycling` 的地形；条件不明确时服务端返回 `NAV_MOVEMENT_UNAVAILABLE`，不会猜测上下车或背包状态。规划响应的 `movement` 字段会返回选择结果、可用方式、原因和证据。

同方向连续格会压缩为一个方向保持段发送给 BizHawk，转弯处和每段末尾仍做 PlayerRuntime 落点验证。执行任务返回的 `continuous_segments` 会记录按钮、帧数、段首尾和验证结果。

规划和执行分别写入 `logs/navigation_plans.jsonl`、`logs/navigation_execution.jsonl`，也可通过只读 `GET /api/v1/navigation/logs?kind=plan|execution&limit=` 查询；可附 `task_id` 或 `plan_id` 过滤。请求 schema 校验失败也会落入对应日志，因此“规划成功、开始执行时报 The navigation request is invalid”这类问题可以直接按计划/任务 ID 回溯。

`GET /api/v1/navigation/observations?zone_id=&x=&y=&z=&limit=` 返回一个有界的、只包含直接观测有向边的同 Zone 分量。`x/y/z` 是可选的起点选择锚点，但三者必须一起提交；响应始终带 `execution_eligible=false`，用于静态地图选择可演示的起点和目标，不能证明玩家目前位于 `start`。

`POST /api/v1/navigation/plans` 只生成可绘制路径，不发送游戏输入。必填 `destination` 使用完整的 `gen5-field-grid-v1` 坐标；可选 `start` 使用完全相同的坐标结构。省略 `start` 或传 `null` 时，服务端从 resolved PlayerRuntime 解析起点。指定 `start` 时可以在没有实时玩家样本的情况下预览已知地图中的路线：

```json
{
  "start": {
    "type": "grid",
    "space": "gen5-field-grid-v1",
    "zone_id": 439,
    "x": 117,
    "y": 1,
    "z": 694
  },
  "destination": {
    "type": "grid",
    "space": "gen5-field-grid-v1",
    "zone_id": 439,
    "x": 118,
    "y": 1,
    "z": 694
  }
}
```

`start` 和 `destination` 都要求 `type=grid`、`space=gen5-field-grid-v1` 以及整数 `zone_id,x,y,z`；不接受省略高度层的二维坐标。`explicit_grid` 是服务端返回的起点来源名，不是请求中的 `type` 值。`GET /api/v1/navigation/capabilities` 的 `planning.start_types` 声明支持的来源。

| 起点方式 | `resolved_start.source` | 响应含义 |
|---|---|---|
| 省略 `start` 或传 `null` | `player_runtime` | 使用已解析玩家坐标，并保留样本 `frame` 和 `confidence`。|
| 提交完整 `start` | `explicit_grid` | 调用方提供的预览坐标，`frame=null`、`confidence=candidate`，不能证明玩家在此处。|

指定起点不会补出未观测的可走边。当前两种规划方式都只支持同 Zone、具有直接观察证据的有向路径；缺少路线返回 `409 NAV_NO_ROUTE`，跨 Zone 返回 `409 NAV_TRANSITION_UNVERIFIED`。无效或多余的请求字段返回 `422 NAV_INVALID_REQUEST`。示例坐标只有在对应路径已被观测时才能得到路线。

`POST /api/v1/navigation/tasks` 接受 `destination`、可选 `max_steps`（1–2000，默认 2000）和 `movement_mode`，不接受 `start` 或 `plan_id`。执行器从实时玩家坐标重新规划并检查可控性；即使预览返回 `status=ready`，`resolved_start.source=explicit_grid` 也只表示路线可展示，前端必须保持该预览的执行入口不可用。准备执行时，先读取实时状态、省略 `start` 重新规划，再提交任务。当前跨 Zone 执行尚未开放，ROM Warp 候选和真实落点的验证是后续启用条件。

建议动作循环：读取 `game/state` → 读取 `ai/map/tile` 或 `ai/map/scene` → 生成 plan → 每一步输入后重新读取 state → 位置未按预期变化即暂停并重新规划。

## AI 使用约束

1. 永远携带 `zone_id` 和坐标空间，禁止把不同 Zone 的 `(x,z)` 直接比较。
2. `candidate`/`unverified` 只用于候选排序，不可当作“可走”“草丛”“出口已确认”。
3. 看到 warp 时先执行过渡观测，再把 `destination.tile` 从 null 升级为 verified。
4. 原始 `model_id`、permission byte 和 TileType 要保留在决策日志，便于后续证据升级。
