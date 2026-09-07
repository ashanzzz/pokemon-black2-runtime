# Matrix Global Navigation v13 实机测试

## A. 离线测试

```powershell
python -m pytest -q
node --check frontend/workbench.js
node --check frontend/world3d-runtime-fixed.js
```

本交付基线预期所有测试通过。

## B. API 能力检查

打开：

```text
GET /api/v1/navigation/capabilities
```

应看到：

```text
coordinate_spaces 包含 gen5-matrix-grid-v1
planning.cross_zone = true
planning.cross_matrix = false
```

## C. 同 Matrix 跨 Zone 远距离规划

1. 玩家进入一个连续室外大 Matrix。
2. Workbench 可勾选“拼接相邻室外”，方便看远处 Tile。
3. 导航面板不再需要填写 Zone。
4. 选择一个明显位于另一个相邻 Zone 的远处可走地面。
5. 点击“规划路线”。

计划应满足：

```text
resolved_start.global.matrix_id == resolved_goal.global.matrix_id
segments[0].kind == matrix_global
cost.zone_transitions >= 1
route_detail.nodes 连续
每两个相邻节点 Manhattan distance == 1
```

如果 route_source 是 candidate_static，还可以查看：

```text
decoded_zone_count
decoded_zone_ids
```

## D. 执行跨 Zone 路线

1. 点击“开始寻路”。
2. 观察人物穿过 Zone boundary。
3. Runtime `zone_id` 变化时任务应继续 `executing`，不能出现：

```text
NAV_POSITION_DIVERGED
NAV_TRANSITION_UNVERIFIED
```

4. UI 路线不能因为 Zone 改变而消失。
5. 到达后 `arrival.position` 与目标 x/y/z 一致。

请记录：

```text
task_id
起点 Zone / Matrix / GPos
终点 resolved_zone_id / Matrix / GPos
zone_transitions
continuous_segments
最终 arrival
```

## E. 远处地图点击

在拼接地图里直接点击另一个 Zone 的 Terrain。

浏览器 Network 应调用：

```text
POST /api/v1/navigation/global/snap
```

而不是把远端地面强行作为当前 Zone 的 `/navigation/snap`。

返回应有：

```text
coordinate.type = global_grid
resolved_zone_id = 自动解析值
legacy_grid = 内部兼容 metadata
```

然后规划应能从当前玩家位置直接画到该远端目标。

## F. Zone-less resolve

测试：

```text
GET /api/v1/navigation/global/resolve?x=<目标X>&y=<Y>&z=<目标Z>
```

实时玩家可用时无需 `matrix_id`。

再测试显式 Matrix：

```text
GET /api/v1/navigation/global/resolve?matrix_id=<当前Matrix>&x=<目标X>&y=<Y>&z=<目标Z>
```

两者应得到相同 `resolved_zone_id`。

## G. 独立室内 Matrix

在一个 standalone indoor Matrix 中：

1. Matrix 可以由当前玩家自动推断。
2. 即使 Matrix 没有 Zone ownership table，只要当前 Zone 确实引用该 Matrix，Zone-less 坐标仍可解析为当前 Zone。
3. 不允许把另一个 Matrix 中相同 `(x,z)` 当作同一地点。

## H. 跨 Matrix 负向测试

显式给一个不同 `matrix_id` 的 global destination。

当前预期：

```text
NAV_MATRIX_TRANSITION_UNVERIFIED
```

这是正确结果，不是 bug。

这一边界用于避免不同室内 Matrix 的相同局部坐标发生冲突。真正跨 Matrix 自动导航应在 Warp/door landing connector 被验证后再打开。

## I. 回传给我的最有价值数据

如果长距离路线出现失败，请发：

```text
1. /api/v1/navigation/plans 请求 + 响应
2. task 最终 JSON
3. /api/v1/navigation/logs?kind=execution&limit=200&task_id=<id>
4. 起点和终点 Matrix / resolved Zone / GPos
5. zone_transitions[]
6. 失败时 PlayerRuntime
7. 3D 路线截图
```
