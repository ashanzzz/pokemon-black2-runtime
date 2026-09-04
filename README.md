# Pokémon Black 2 · AI Semantic Runtime v4

面向《宝可梦 黑2》IREJ / 日版 v1.1 的只读 RAM 运行时观察与控制研究项目。

本版本重点完成前后端事实源统一：浏览器模块不再各自解释“在线/离线、朝向、地图”，而是共享一个 Runtime Hub；地图系统使用当前 RAM 的 Field/Player/G3DMapper/Prop/Actor 结构锚定 ROM Matrix/Zone/Event，再输出带证据等级的 World Scene。

## 端口

- `127.0.0.1:8765` — FastAPI + Web UI (HTTP)
- `127.0.0.1:8766` — BizHawk Lua Bridge (TCP)

网页全部使用同源相对 API 路径，不硬编码 HTTP 端口。

## 启动

```powershell
python -m pip install -r requirements.txt
set BLACK2_ROM_PATH=D:\path\to\your\PokemonBlack2_IREJ_v1.1.nds
python run_runtime.py
```

然后 BizHawk：

1. Tools → Lua Console
2. Open script → `bridge/bizhawk/black2_bridge.lua`
3. 浏览器打开 `http://127.0.0.1:8765/`

ROM 不是 Runtime/Dialogue/Player 启动的硬依赖；未配置 ROM 时 World/3D 子系统会独立显示 `rom_unavailable`。

## 主要页面

- `/` — Runtime 总览
- `/frontend/player-state.html` — 主角朝向/运动/Tile/Root chain
- `/frontend/dialogue-inspector.html` — 对话/Printer/Choice/Timeline
- `/frontend/map-runtime.html` — BMD0/Prop/Door/Warp/NPC 证据模型
- `/frontend/native-map.html` — 原生 3D BMD0/BTX0 查看器
- `/frontend/trainer-state.html` — 训练家 Save/GameData 域
- `/frontend/controller.html` — 输入控制
- `/frontend/memory-tracer.html` — 只读内存实验
- `/frontend/ram-dumper.html` — 完整证据快照

## 关键 API

```text
GET  /api/v1/runtime/health
GET  /api/v1/runtime/snapshot
POST /api/v1/runtime/refresh
GET  /api/v1/player/runtime
GET  /api/v1/map/runtime/field
GET  /api/v1/map/truth/current
GET  /api/v1/map/scene/current
```

旧 `/api/state`、`/api/bizhawk/status`、`/api/observer/presentation` 仍保留兼容，但它们不再独立制造第二套事实。

## 证据原则

- RAM 当前运行时事实优先。
- ROM 只在当前 Matrix/Map Header/资源身份由 RAM 锚定后加入。
- `unresolved` 不用默认值填充。
- `candidate` 不升级成 `verified`，除非结构、生命周期或受控行为实验支持。
- 截图可用于人工校验，但不是 parser 输入。
- 不写游戏内存。

详见：

- `docs/UNIFIED_RUNTIME_ARCHITECTURE_CN.md`
- `docs/DATA_NEEDED_NEXT_CN.md`
- `docs/V4_VALIDATION_REPORT.json`
