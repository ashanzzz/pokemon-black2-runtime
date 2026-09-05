# Black 2 AI 语义 API v1

本文档定义让外部 AI 读取、理解并规划《口袋妖怪黑 2》运行时的稳定接口。实现位于 `backend/black2/api/semantic_routes.py`，统一前缀为 `/api/v1`。

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

`GET /api/v1/ai/context` 返回一个有界上下文文档，聚合 state、party、inventory 和 map，适合每个决策周期直接提供给 AI。

## 地图和单格语义

`GET /api/v1/ai/map` 返回当前已加载矩阵、驻留区块、玩家、NPC、家具、触发器、ROM warp 记录和碰撞证据。加 `include_raw=true` 才返回完整 permission plane；加 `text=true` 返回 `BLACK2_MAP_SCHEMATIC/v1` 文本，适合无 JSON 工具的模型。

`GET /api/v1/ai/map/tile?zone_id=&x=&y=&z=` 查询单个格。响应固定包含：

- `rom.model_id`、`rom.permission_planes`：原始模型和权限字节。
- `terrain.kind` / `terrain.label`：只有已验证时才填真实材质；通常为 `unknown`，同时提供 `candidate_kind`、`candidate_label`。
- `collision.can_walk`：只有行为证据足够时才可为布尔值；`candidate_can_walk` 是保守分类结果。
- `interactions`：例如 `warp` 的入口候选。
- `runtime_tile_type`：玩家当前脚下 TileType（只在当前格可用）。

P00 权限字节的候选分类包括 `walkable_candidate`、`blocked_candidate`、`water_candidate`、`ledge_*_candidate` 和 `warp_door_candidate`。分类来自 `navigation.classify_collision_byte`，不是最终材质真值；路径执行必须在动作后重新读取位置确认结果。

跨 Zone 查询会返回 `status=outside_loaded_zone`、`terrain.kind=unknown`、`collision.can_walk=null`，避免把另一张地图的数据误用到当前地图。

## 入口、出口与跨地图图

`GET /api/v1/ai/map/portals` 读取当前场景的 warp/door 候选。每个记录含源地图和格、目标 `map_header_id`/`warp_id`、`destination.tile`（尚未观测时为 null）以及 `requires_transition_observation`。

`GET /api/v1/ai/map/warps?zone_id=` 返回 ROM 全局连接图。每条边含 source、候选 destination、反向边索引和 verification；`landing_tile` 必须等实时 Zone 迁移观测后才能填充。

`GET /api/v1/ai/map/zone/{zone_id}` 返回单个 Zone 节点及其连接器。

`GET /api/v1/ai/map/scene` 返回供地图 UI 或规划器使用的完整规范化场景图：`normalized.portals`、`doors`、`actors`、`geometry`、`collision`。

## 队伍和背包

`GET /api/v1/game/party` 与 `GET /api/v1/game/inventory` 已固定响应格式。若 `decode_status=unverified`，`slots/items=[]` 的含义是“尚未解码”，不是队伍或背包为空；AI 不得据此做资源决策。字段列表预先固定，完成 RAM 逆向后可无破坏地填充。

## 路径规划和执行

读取地图后，将目标编码为 `gen5-field-grid-v1` 的 `destination`，调用现有 `/api/v1/navigation/plans` 生成可绘制路径，再调用 `/api/v1/navigation/tasks` 执行。规划器只使用已观测移动边；跨 Zone 先读取 `/ai/map/warps`，只有 connector 和 landing tile 被实时验证后才允许执行。

建议动作循环：读取 `game/state` → 读取 `ai/map/tile` 或 `ai/map/scene` → 生成 plan → 每一步输入后重新读取 state → 位置未按预期变化即暂停并重新规划。

## AI 使用约束

1. 永远携带 `zone_id` 和坐标空间，禁止把不同 Zone 的 `(x,z)` 直接比较。
2. `candidate`/`unverified` 只用于候选排序，不可当作“可走”“草丛”“出口已确认”。
3. 看到 warp 时先执行过渡观测，再把 `destination.tile` 从 null 升级为 verified。
4. 原始 `model_id`、permission byte 和 TileType 要保留在决策日志，便于后续证据升级。
