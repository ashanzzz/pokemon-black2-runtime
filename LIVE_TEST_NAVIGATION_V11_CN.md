# v11 实机测试步骤

## A. 更新后先做离线测试

在仓库根目录：

```powershell
python -m pytest -q
```

本交付包基线结果：

```text
260 passed, 6 skipped
```

## B. 测“只走过去，不互动”

1. 启动 BizHawk、Bridge、Backend、Workbench。
2. 浏览器 `Ctrl + Shift + R` 强制刷新。
3. 打开底部“导航”。
4. “导航语义”选择 **纯移动（走到该格，不互动）**。
5. 移动方式先选 `auto`。
6. 输入一个明确空地坐标，例如你原来测试的 `(7,0,5)`，或直接点击空地。
7. 先点“规划路线”，检查：
   - `navigation_intent = walk_to_tile`
   - plan 中没有 `interaction`
   - plan 中没有 `actor_id`
   - `cost.steps`、`cost.turns` 正常
8. 点“开始寻路”。
9. 观察角色连续行走/跑步，不应该每格停顿。
10. 任务结束应为 `succeeded`，不能再出现 `NAV_DYNAMIC_TARGET_LOST`。

如果你输入的目标格**当前确实站着 NPC**，精确坐标模式应该返回 `NAV_DESTINATION_OCCUPIED`。这是正确行为，因为玩家不能与 NPC 占同一格。想“走到 NPC 附近但不说话”，请用纯移动模式直接点击 NPC；`snap` 会返回相邻空地。

## C. 测 NPC 点击但不互动

1. 保持“纯移动”。
2. 点击一个 NPC。
3. 前端选中的目标应是 NPC 旁边的空地。
4. 执行后只到达该空地；不能按 A，不能追踪 NPC。

## D. 测真正 NPC 互动

1. “导航语义”切换为 **走到 NPC 旁并互动**。
2. 点击 NPC，或从“已知坐标”下拉选择 NPC。
3. 规划结果必须包含：`interaction_target / stand_tile / facing / actor_id`（如果 ActorID 可用）。
4. 执行应：走到 stand_tile → 转向 → 按 A。
5. 如果 NPC 在任务期间移动，只有这类任务允许动态重新规划。

## E. 测移动形态

### OnFoot + 允许跑步的 Zone

`movement_mode=auto` 的 plan 应显示：

```json
{"selected":"run"}
```

角色应按 `B + 方向` 连续跑。

### 已经骑车

手动先骑上自行车，再规划 `auto`。若 Zone/路线允许骑车：

```json
{"selected":"bike"}
```

### 已经 Surf

手动进入 Surf，再规划 `auto`：

```json
{"selected":"surf"}
```

本版不会自动帮你完成骑车/下车/开始 Surf 的菜单动作。

## F. 测整体室外地图

1. 到室外。
2. Workbench 顶部勾选 **拼接相邻室外**。
3. 观察 3D 场景是否一次显示同 Matrix 中相邻 Zone。
4. 打开：

```text
http://127.0.0.1:8765/api/v1/map/v6/scene/connected/current
```

重点检查：

- `connected_world.matrix_id`
- `connected_world.zone_ids`
- `connected_world.adjacency`
- `connected_world.bounds_world`
- `static.terrains[*].source_zone_id`
- `static.buildings[*].source_zone_id`

5. 再打开：

```text
http://127.0.0.1:8765/api/v1/map/v6/world/cluster/<当前ZoneID>
```

检查 `connectors.outgoing_edges` 是否同时给出了 Warp 等跨 Matrix 连接。

6. 走过同 Matrix 的 Zone 边界时，整体场景不应重新跳到另一套原点；主角应该在拼接场景里连续移动。

## G. 建议回传给我

如果还有异常，打包以下信息：

1. `/api/v1/map/v6/player/live`
2. `/api/v1/map/v6/scene/connected/current`
3. `/api/v1/map/v6/world/cluster/<zone>`
4. `/api/v1/navigation/plans` 的请求和响应
5. 失败 task 的 `/api/v1/navigation/tasks/<task_id>`
6. `/api/v1/navigation/logs?kind=execution&limit=100&task_id=<task_id>`
7. 一张 Workbench 3D 截图

特别是遇到 `NAV_STUCK` / `NAV_POSITION_DIVERGED` 时，把 `continuous_segments` 和 `landing_diagnostics` 一起发回。
