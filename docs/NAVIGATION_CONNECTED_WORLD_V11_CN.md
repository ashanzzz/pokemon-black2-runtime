# Navigation / Connected World v11 设计与接口

## 1. 本次修复的核心约束

### 1.1 纯移动与互动彻底分离

公共导航语义只建议使用：

- `walk_to_tile`：纯移动。只到达目标格，绝不自动对 NPC 按 A。
- `interact`：显式 NPC 互动。先选择 NPC，再寻找相邻站位格、调整朝向，最后按 A。

`route` 仅保留为旧客户端兼容的纯移动别名，不再拥有“自动识别 NPC 并改成互动”的行为。

因此，目标 `(7,0,5)` 即使恰好被 NPC 占用：

- `walk_to_tile` 不会变成 NPC 跟踪任务；
- 如果要求精确站在被占用的格子，返回 `NAV_DESTINATION_OCCUPIED`；
- 如果是 UI 点击 NPC/物体但仍处于纯移动模式，`/snap` 会寻找附近可站立地面，而不是互动；
- 只有 `navigation_intent=interact` 才建立动态 NPC target。

### 1.2 运动形态

`movement_mode`：

- `auto`
- `walk`
- `run`
- `bike`
- `surf`

`auto` 只使用**当前已经激活、且有证据允许执行**的最快形态：

1. 已处于 `Cycling` 且路线允许骑车 → `bike`
2. 已处于 `Surf` → `surf`
3. `OnFoot` 且 ZoneHeader 允许跑步 → `run`
4. 否则 → `walk`

导航不会为了 `auto` 擅自打开菜单、骑车、下车或启动 Surf；这些状态切换必须等各自的 verified skill 完成后再接入。

## 2. 最优路径与平滑执行

### 2.1 路径目标

静态 ROM fallback 使用 A*，优化目标改为词典序：

1. **最少格数**
2. 在最短路径集合中，**最少转向次数**

也就是说，不会为了路线“看起来直”而绕远路，但在一样短的路线里会优先少拐弯的路线。

计划响应新增：

```json
{
  "cost": {
    "steps": 18,
    "turns": 3,
    "connectors": 0
  },
  "route_detail": {
    "optimization": "shortest_steps_then_fewest_turns"
  }
}
```

### 2.2 执行器

执行器仍保持闭环安全，而不是把整条路线一次性盲发给 BizHawk：

- 连续同方向节点合并为一个 segment；
- segment 内持续按住方向，不做“一格一停”；
- 每个 segment 终点仍通过实时 PlayerRuntime GPos 验证；
- 到达预期终点立即 `clear_inputs`，防止冲过头；
- 转向时增加有界输入预算，避免第一帧只完成转身却没真正迈步；
- 动态 NPC 重规划只允许发生在 `interact` 任务中。

这是“观感平滑”和“闭环不失控”之间的当前折中。

## 3. 室外整体地图拼接

### 3.1 什么可以直接拼

只有满足以下条件的 Zone 才会被空间拼接：

- 室外；
- 属于同一个 ROM `MapMatrix`；
- Matrix 有 Zone ownership table；
- 两个 Zone 的实际占用 Chunk cell 上下左右相邻。

这些 Zone 天然共享 `gen5-field-world-v1`，无需人为加 offset。

### 3.2 什么不能直接拼

仅通过 Warp/门连接、但处于不同 Matrix 的地图，不会强行摆到同一个 3D 坐标平面。

API 会把这些关系保留在 `connectors.outgoing_edges`。未来只有在运行时验证入口/落点 transform 后，才可以升级成跨 Matrix 的空间布局或跨 Zone 自动执行。

## 4. 新 API

### 当前玩家所在的整体室外场景

```http
GET /api/v1/map/v6/scene/connected/current
```

用于前端 3D 渲染。室内会自动退化成单 Zone，不强行拼接。

### 指定 Zone 的整体室外静态预览

```http
GET /api/v1/map/v6/scene/connected/zone/439
```

无需主角处于该地图即可读取。

### 给 AI / 第三方读取的整体地图 bundle

```http
GET /api/v1/map/v6/world/cluster/439
```

主要字段：

```json
{
  "format": "black2-connected-world-api/v1",
  "anchor_zone_id": 439,
  "connected_world": {
    "matrix_id": 0,
    "zone_ids": [439, 440],
    "adjacency": [],
    "cells": [],
    "bounds_world": {},
    "scene_origin": {}
  },
  "static": {
    "terrains": [],
    "buildings": [],
    "entities": {}
  },
  "connectors": {
    "nodes": [],
    "outgoing_edges": []
  }
}
```

实际 `zone_ids` 由 ROM 决定，上例数字仅表示字段形式，不应当作为 Zone 439 的真实连接结论。

## 5. 导航 API 推荐用法

### 纯移动到坐标

```http
POST /api/v1/navigation/plans
Content-Type: application/json
```

```json
{
  "destination": {
    "type": "grid",
    "space": "gen5-field-grid-v1",
    "zone_id": 441,
    "x": 7,
    "y": 0,
    "z": 5
  },
  "navigation_intent": "walk_to_tile",
  "movement_mode": "auto"
}
```

执行：

```http
POST /api/v1/navigation/tasks
```

请求结构相同，可加 `max_steps`。

### UI 点到 NPC，但只想走过去

保持：

```json
{"navigation_intent":"walk_to_tile"}
```

调用：

```http
POST /api/v1/navigation/snap
```

`snap` 会把 NPC 占位格当障碍，返回附近可站立格，不创建 interaction。

### 真正与 NPC 互动

先把 UI 语义切换到 `interact`，然后点击 NPC。`/snap` 返回：

- `interaction_target`
- `stand_tile`
- `facing`
- `interaction`

再把这份 interaction 交给 plan/task；执行器到达相邻格、验证朝向后才按 A。

## 6. 当前明确不做的事情

- 不自动骑车/下车；
- 不自动启动/退出 Surf；
- 不把不同 Matrix 的地图凭猜测叠在一起；
- 不把 Warp ROM candidate 当成已经验证的跨 Zone 执行路径；
- 不取消实时 GPos 落点验证来换取“更快”的假流畅。

跨 Zone 自动执行下一阶段应建立在 connector runtime observation 上，而不是直接放开现在的 `NAV_TRANSITION_UNVERIFIED`。
