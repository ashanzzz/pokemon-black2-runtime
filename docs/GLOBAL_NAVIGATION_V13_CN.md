# Matrix Global Navigation v13

## 1. 为什么不再要求用户输入 Zone

Pokémon Black 2 的很多连续室外区域位于同一个 `MapMatrix`。这些 Zone 的 Terrain Chunk 已经使用同一套 Gen5 world/grid 坐标，所以 Zone 边界不是空间断点。

v13 将公开导航地址从：

```text
(zone_id, x, y, z)
```

提升为：

```text
(matrix_id, x, y, z)
```

其中 `matrix_id` 在实时游戏中也可以省略，由当前 PlayerRuntime 自动推断。

因此推荐的导航目标是：

```json
{
  "type": "global_grid",
  "space": "gen5-matrix-grid-v1",
  "x": 135,
  "y": 0,
  "z": 625
}
```

用户、AI 和前端不需要知道目标属于哪个 Zone。后端根据 ROM Matrix cell ownership 自动得到 `resolved_zone_id`。

## 2. Zone 没有删除，只是不再属于地址

Zone 仍用于：

- Runtime Actor / NPC 归属；
- Script / Trigger / Warp 语义；
- 日志和错误诊断；
- Matrix ownership 证据；
- 进入另一个 Matrix 时的 transition verification。

所以 v13 的原则是：

```text
Zone = metadata / runtime ownership
Matrix + GPos = spatial address
```

这避免了两个不同独立室内场景都存在 `(5,7)` 时发生坐标碰撞。

## 3. Same-Matrix 跨 Zone A*

`RomStaticNavigationGraph.find_global_path()` 现在可以跨 Zone 搜索，只要：

1. 起点和终点属于同一个 Matrix；
2. 目标 Matrix cell 有 ROM Zone ownership，或 standalone Matrix 能由当前 Zone 唯一确认；
3. 起点与终点处于同一 GPos.y 层；
4. 每个探索 Tile 都是静态 flag-clear candidate；
5. direction barrier / ledge / actor occupancy 等约束仍通过。

路径节点仍保留 `zone_id`，但 Zone 改变不会产生 connector cost。

返回示例：

```json
{
  "segments": [
    {
      "kind": "matrix_global",
      "matrix_id": 0,
      "steps": 42
    }
  ],
  "zone_transitions": [
    {
      "from_zone_id": 439,
      "to_zone_id": 440,
      "at": {"x": 120, "y": 0, "z": 690}
    }
  ]
}
```

`zone_transitions` 只是运行日志和 UI metadata，不是 Warp。

## 4. 懒加载，而不是把整张大 Matrix 先展开

大世界 Matrix 不会在每次请求时一次性解析成完整 A* 网格。

A* 探索一个坐标时：

```text
(x,z)
  ↓
Matrix cell
  ↓
ROM Zone ownership
  ↓
只加载该 Zone 对应 y 层的静态导航 Cell
```

因此长距离路线只解码搜索真正经过的 Zone。响应提供：

```text
decoded_zone_count
decoded_zone_ids
```

方便性能诊断。

## 5. API

### 5.1 直接规划 Zone-less 目标

```http
POST /api/v1/navigation/plans
```

```json
{
  "destination": {
    "type": "global_grid",
    "space": "gen5-matrix-grid-v1",
    "x": 135,
    "y": 0,
    "z": 625
  },
  "movement_mode": "auto",
  "navigation_intent": "walk_to_tile"
}
```

如果 PlayerRuntime 可用，不需要 `matrix_id`。

### 5.2 执行

```http
POST /api/v1/navigation/tasks
```

请求结构相同。v13 将 `max_steps` 上限提高到 10000，适合长距离 same-Matrix 路线。

### 5.3 把全局坐标解析成内部 Zone metadata

```http
GET /api/v1/navigation/global/resolve?x=135&y=0&z=625
```

也可以显式：

```http
GET /api/v1/navigation/global/resolve?matrix_id=0&x=135&y=0&z=625
```

返回：

```json
{
  "coordinate": {
    "type": "global_grid",
    "space": "gen5-matrix-grid-v1",
    "matrix_id": 0,
    "x": 135,
    "y": 0,
    "z": 625
  },
  "resolved_zone_id": 440,
  "legacy_grid": {
    "zone_id": 440,
    "x": 135,
    "y": 0,
    "z": 625
  }
}
```

### 5.4 3D 拼接地图远距离点击

```http
POST /api/v1/navigation/global/snap
```

纯移动点击使用该接口，因此点击远处拼接 Zone 时不再错误地用“当前 Zone”解释远端 Tile。

NPC `interact` 仍使用 local `/navigation/snap`，因为 NPC target 属于实时 ActorSystem，而不是纯空间目标。

## 6. Executor 如何跨 Zone 不停下来

Navigation Task 的位置判断改为 Matrix spatial equality：

```text
相同 x/y/z
+ 两个 Zone 属于同 Matrix
= 同一个空间 Tile
```

因此玩家越过室外 Zone boundary 时，即便 Runtime Zone 从 A 更新成 B，执行器不会误报 `NAV_POSITION_DIVERGED`。

连续同方向 segment 仍保持 hold input；Zone 标签变化本身不会制造停顿。

Workbench 也不再因为同 Matrix 的 Zone 改变清空路线。只有 Matrix domain 改变才把现有 global route 标记 stale。

## 7. 为什么还不能把所有 Matrix 彻底合成一个 x/y/z

不同 Matrix 可能同时包含完全相同的局部坐标：

```text
Matrix 261 indoor A: (5,7)
Matrix 300 indoor B: (5,7)
```

这些不是同一个空间位置。

因此当前规则：

```text
same Matrix + adjacent Zone ownership
    -> 直接 Matrix-global A*

different Matrix
    -> 必须通过 verified Warp / Door / Connector transition
```

也就是说 v13 已经去掉了不必要的 **Zone barrier**，但没有去掉必要的 **Matrix domain**。

如果未来每个 Warp 都完成 entrance/landing transform 验证，可以在 Matrix-global planner 上再加一层 `WorldConnectorGraph`，实现真正从任意室内/室外到任意目标的一次全局任务。

## 8. 兼容性

旧客户端仍可以继续传：

```json
{
  "type": "grid",
  "space": "gen5-field-grid-v1",
  "zone_id": 439,
  "x": 118,
  "y": 0,
  "z": 694
}
```

v13 不删除 legacy API。

新的 UI 默认不再显示 Zone 输入框，只显示：

```text
Matrix（可自动）
X
Y
Z
```

Zone 只在计划详情、transition 日志和调试信息中显示。
