# World Lab v8 架构

## Scene Graph

```text
WorldRoot
├─ StaticWorld              ROM / exported v5，按 Zone 加载
│  ├─ Terrain
│  └─ Buildings
├─ RuntimeOverlay           RAM
│  ├─ Player
│  └─ NPC Actors (opt-in)
├─ NavigationOverlay        observed layered graph path
└─ DebugOverlay             grid / evidence markers
```

所有对象先进入 `gen5-field-world-v1`，浏览器只做显示原点平移，不修改 canonical truth。

## 防止场景堆叠

Scene 使用 staging root：新场景完整异步加载后才原子替换旧 root。每个 await 返回后重新检查 generation，旧 Zone 的迟到 GLB 不能写进新场景。

每个 scene 的 Three.js GPU 资源独立管理，切场景时释放旧 geometry/material/texture，避免长期切图后的 GPU 泄漏。

## Building diagnostics

每栋建筑保留：UID、DoorUID、world position、asset URL。前端分别统计 `loaded/failed`，并保存具体失败 URL 到校准报告。

## NPC diagnostics

Runtime Actor 使用 bounded actor heap。`runtime model_id → Gen5 OBJCODE` 目前仍是 candidate。v8 会尝试使用该编号查询 MModel registry 并渲染原版 billboard/GLB；失败则明确显示 fallback marker。

## Height

3D 地形高度来自原版模型本身。Player 渲染使用 `FieldActor.WPos.y`。逻辑导航节点使用 `GPos.y` 作为离散 elevation layer，并保留 WPos.y 样本用于后续校准。
