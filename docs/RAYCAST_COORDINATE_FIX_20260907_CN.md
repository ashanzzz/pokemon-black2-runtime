# Zone 441 3D Terrain 点击坐标修复与测试协议

## 已验证基线

当前实测：

```text
Zone ID       441
GPos          (5,0,7)
WPos          (88,0,120)
Idle residual (0,0)
scene_origin  (88,0,120)
Matrix        261
Matrix size   1×1
Chunk         977
Chunk locator (256,0,256)
```

`GPos/WPos`、`scene_origin`、Chunk locator 均不需要修改。

## 根因

Three.js `Raycaster` 返回的 `Intersection.point` 已经是 Three.js world-space 坐标。旧代码在 Terrain GLB 为 `SkinnedMesh` 时又执行一次：

```js
meshPoint.applyMatrix4(pickHit.object.matrixWorld)
```

Zone 441 的 Terrain display translation 为 `(168,136)`。这个平移被重复加入后，可以直接把玩家附近正确的 `(5,7)` 点击推到实测异常 `(16,15)` 或 `(18,15)`。

## 本次修改

只修正点击坐标的基础实现：

```text
frontend/world3d-runtime.js
```

不修改：

```text
FieldActor.GPos
FieldActor.WPos
scene_origin
Terrain item.world
Matrix/Chunk 映射
```

同时加入一次鼠标点击一次的 `[Terrain Click Debug]` 日志。

## 自动测试

在仓库根目录执行：

```powershell
python -m pytest -q tests/test_world3d_raycast_coordinate_fix.py
```

预期：全部通过。

建议再跑全量测试：

```powershell
python -m pytest -q
```

## 实机测试 A：Zone 441 五点测试

1. 启动游戏和 Runtime。
2. 保持主角在 Zone 441 静止。
3. 先确认 `/api/v1/map/v6/player/live` 中：

```text
grid  = (5,0,7)
world = (88,0,120)
```

4. 浏览器按 `Ctrl+Shift+R` 强制刷新。
5. F12 打开 Console，搜索 `Terrain Click Debug`。
6. 点击格子中心，不要点墙、家具、NPC、门框。

推荐顺序和期望：

```text
脚下      (5,0,7)
右一格    (6,0,7)
左一格    (4,0,7)
上一格    (5,0,6)
下一格    (5,0,8)
```

点击靠近 tile 边界时可能自然落入相邻格，因此尽量点击格子中心并观察高亮框。

每条日志都应满足：

```text
raycastPointSpace = three_world
worldX = point.x + scene_origin.x
worldZ = point.z + scene_origin.z
grid.x = floor(worldX/16)
grid.z = floor(worldZ/16)
```

如果 `isSkinnedMesh=true`，也不能再出现额外的 `matrixWorld` 坐标平移。

## 实机测试 B：移动后重复

从 `(5,7)` 实际移动 3 到 5 格。等待主角静止后，再点击：

```text
当前脚下
前后左右相邻格
```

点击坐标必须跟着 RAM GPos 同步移动，不能固定漂在 `16~18` 附近。

## 实机测试 C：跨场景

至少再测试：

- 一个室外多 Chunk 场景
- 另一个室内场景
- 楼梯或有高度变化的场景

判定重点：

1. X/Z 邻格每次变化 1 tile。
2. 不再出现固定约 16 tile 的偏移。
3. NPC/door/warp 点击仍然使用其 semantic WPos。
4. Terrain cell 高亮与实际地面一致。
5. 导航目标选格与显示的 GPos 一致。

## 请回传的最小证据

测试后请发：

1. Zone 441 五次 `[Terrain Click Debug]` 完整对象。
2. 同时刻 `/api/v1/map/v6/player/live` JSON。
3. `/api/v1/map/v6/scene/current` 中 `scene_origin` 和 terrain `item.world`。
4. 一张 3D 页面截图，能看到主角和点击格高亮。
5. 如果某一格仍错，说明你点的是脚下/上/下/左/右中的哪一个。

有这些数据即可继续判断是否还存在第二个独立问题。
