# 下一轮最有价值的证据

当前方向不需要继续测。四方向 FaceDir + RotationAngle 已 4/4 验证。

## A. Walk / Run（用于最终确定 gait）

各提供 2 份完整 Runtime Evidence：

1. `WALK_STABLE_1`：不按 B，已经连续走动约 0.5–1 秒，在仍移动时导出。
2. `WALK_STABLE_2`：同上，换另一个方向。
3. `RUN_STABLE_1`：按住 B + 方向，已经持续跑动，在仍移动时导出。
4. `RUN_STABLE_2`：同上，换另一个方向。

用途：
- 校准 WPos / frame 真实速度；
- 同时检查 MovementFlags、NextAcmd、GridStatus、GridCommand、PlayerState 未命名字段是否存在更直接的 gait signal。

## B. 一扇建筑门（地图系统优先级最高）

选一个你肉眼明确知道“这是门、会进建筑”的入口，最好同一地点连续采：

1. `DOOR_OUTSIDE_BEFORE`：站在门外正前方，面向门，完全静止。
2. `DOOR_ENTER_TRANSITION`：按方向键触发进入后的 fade/transition 中（能抓到最好，没有也不强求）。
3. `DOOR_INSIDE_AFTER`：进入室内后完全稳定。
4. `DOOR_INSIDE_BEFORE_RETURN`：站在室内出口前。
5. `DOOR_OUTSIDE_AFTER_RETURN`：返回室外后稳定。

Operator notes 只写可观察事实，例如：

> 室外，面向上，下一次 Up 会进入建筑。

不要在备注里猜 Map ID、Warp ID、DoorUID。

用途：
- 验证 `FieldPropResInfo.DoorUID` 与 ROM Warp 的空间对应；
- 验证 raw target_map_id / target_warp_id；
- 建立入口双向图；
- 确认切图时 Field / Mapper / Chunk / Actor 生命周期变化。

## C. Chunk 边界

同一直线路径：

- `CHUNK_BEFORE`
- `CHUNK_AFTER`
- `CHUNK_STABLE_AFTER`

用于确认 G3DMapper 的流式窗口和 loaded Chunk 生命周期。

## D. NPC 移动

同一个会走动的 NPC，在两个明显不同位置各导一份稳定快照。用于把 ROM static NPC spawn 与 live FieldActor 对齐。

## E. ROM

程序本机只需要配置：

```powershell
set BLACK2_ROM_PATH=D:\你的路径\PokemonBlack2_IREJ_v1.1.nds
```

不需要把完整 ROM 发进源码存档。如果需要离线分析，可以另外提供从你合法持有 ROM 提取的以下资源包：

- `a/0/0/8`
- `a/0/0/9`
- `a/0/1/2`
- `a/0/1/4`
- `a/1/2/6`
