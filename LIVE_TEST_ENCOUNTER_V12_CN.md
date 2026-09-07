# Encounter Region v12 实机测试

## A. 离线检查

在项目根目录：

```powershell
python -m pytest -q
node --check frontend/workbench.js
node --check frontend/world3d-runtime-fixed.js
```

## B. UI 区域识别

1. 正常启动 BizHawk、Bridge、Backend、Workbench。
2. 浏览器执行 `Ctrl + Shift + R`。
3. 去一个有普通草/深草的室外地图。
4. 可选勾选“拼接相邻室外”。
5. 勾选“遇敌区域”。
6. 底部自动切到 Encounter Dock。
7. 检查 3D 半透明区域、边界线、左侧 Region 列表是否与草地实际形状一致。

记录：

```text
当前 Zone/GPos:
connected world: on/off
region_count:
选中的 region_id:
tile_count:
evidence:
recommended_strategy:
```

特别检查异形草丛：不要只看 bounds；overlay 必须只覆盖 exact tiles。

## C. API 检查

```text
GET /api/v1/encounters/capabilities
GET /api/v1/encounters/regions/current?connected=false
GET /api/v1/encounters/regions/current?connected=true
```

确认 grass 为 verified；water 如果出现应为 probable / surf_candidate，而不是 verified encounter。

`/profiles` 和 `/search` 当前返回 research 是预期行为。

## D. 两格/细长草巡逻

优先选择 `patrol.possible=true` 的同 Zone 草地。

1. UI 选 Region。
2. Strategy 先用 `auto`。
3. Movement 先用 `auto`。
4. 点“开始巡逻”。
5. 观察任务从 `entering_region`（若需要）进入 `patrolling`。
6. 检查玩家每次真实 GPos 都属于 `region.tiles`。

两格 Region 预期策略：

```text
A -> B -> A -> B ...
```

长条 Region：两端往返。

2×2 或更大、存在最小方环：短循环。

## E. 安全停止

巡逻时手动触发一个会结束可控 OVERWORLD 状态的事件（例如正常遇敌，如果当前游戏条件允许）。

预期：

```text
Encounter Task = interrupted
stop_reason.code = ENCOUNTER_OVERWORLD_INTERRUPTED
battle_confirmed = false
```

此时 `battle_confirmed=false` 是正确结果：当前版本只负责安全停止，不声称已经验证 Battle Runtime。

## F. Surf 候选

若选择 `surf_candidate`：

- 未处于 Surf：任务必须拒绝，`ENCOUNTER_SURF_NOT_ACTIVE`。
- 已处于 Surf：才允许 region patrol。
- UI/API 仍应显示 probable，而不是 verified。

## G. 拼接地图边界

开启“拼接相邻室外”后，其他 Zone 的 Region 可以显示和读取，但 Start 按钮必须不可执行。

当前版本禁止用 Encounter Task 跨 Zone 自动跑过去，这是有意的安全边界。

## H. 发回验收数据

建议把这些发回来继续调优：

```text
1. /api/v1/encounters/regions/current?connected=true 完整 JSON
2. 一张 3D Region overlay 截图
3. 一个 region_id + tiles[] + patrol
4. 一个 patrol task 最终 JSON
5. 实际运动观感：转角是否顿、两点往返是否停顿、run/bike/surf 哪个模式
```
