# Pokémon Black 2 Runtime Workbench v8

面向《宝可梦 黑2》IREJ / 日版 v1.1 的只读 RAM 观察、ROM 原版世界重建与逆向诊断项目。

v8 的重点不是继续增加独立测试页，而是把地图相关问题集中到一个 **World Lab** 中排查：3D 地形、建筑、主角、Runtime NPC、分层寻路、校准和可导出报告使用同一套坐标与证据等级。

## 最简单的启动方式

Windows 双击：

```text
BLACK2_LAUNCHER.cmd
```

第一次只需要选择：

1. BizHawk 的 `EmuHawk.exe`
2. 你自己合法持有的 Pokémon Black 2 `.nds`

路径只保存在本机 `runtime/runtime.local.json`，不会进入 Git。若项目没有 `.venv`，启动器会第一次自动创建并安装 `requirements.txt`。

以后直接点击“一键启动”。停止时双击 `STOP_BLACK2.cmd`，它只停止本项目后端，并尝试正常关闭由启动器启动的 BizHawk，不强杀模拟器。

## 核心页面

- `/` — Runtime 总览
- `/original-map` — **World Lab**，主地图与排错入口
- `/frontend/player-state.html` — Player Runtime 细节
- `/frontend/dialogue-inspector.html` — Dialogue / Printer / Timeline
- `/runtime-monitor` — HTTP / Bridge / 版本 / 服务状态
- `/ram-dumper` — 显式全量证据采集
- `/frontend/controller.html` — 输入控制
- `/frontend/memory-tracer.html` — 内存实验

旧的 `native-map.html`、`map-runtime.html`、`navigation.html` 等重复地图页已从发布版删除，避免同一个事实被多套前端重复解释。

## World Lab 的事实分层

### 静态 ROM / exported world

不需要实时读取：Zone、Area、Matrix、Terrain、Building placement/model、Door metadata、静态 NPC/Warp/Trigger 定义。

### Runtime RAM

只读取真正变化的内容：Player WPos/GPos、朝向、移动、Zone、Runtime Actor、Prop 生命周期。

### 性能策略

- Player 页面高频读取 **RuntimeHub 缓存**，不额外扫描 Main RAM。
- NPC 默认关闭；打开时只读 ActorSystem + bounded actor heap。
- 3D Scene 只在第一次解析 Player、Zone 变化、手动刷新时重建。
- legacy NativeMap 周期性 4 MiB 扫描默认关闭。
- 旧二维自动寻路默认禁止执行。

## 桥、楼梯、楼层与寻路

v8 新增 `Observed 3D Navigation Graph`。节点不是 `(x,z)`，而是：

```text
(zone_id, GPos.x, GPos.y, GPos.z)
```

因此桥上和桥下即使 X/Z 相同，也属于不同高度层。你真实走过一个 tile transition 后，系统记录这条可通行边；楼梯/坡道会记录 elevation change。

ROM permission byte 仍可作为候选研究材料，但在语义没有验证前不会被当成执行级真值。

## 校准与给 ChatGPT 的报告

World Lab → `校准`：

1. 选择测试类型，例如室外、室内、桥、楼梯、门、建筑、NPC。
2. 点击“开始录制”。
3. 在 NDS 里正常走动。
4. 点击“结束并导出”。
5. 下载生成的 `calibration_*.zip` 发到后续对话。

报告包含：

- 每个样本的 GPos / WPos / Zone / Chunk / Facing
- Grid→World 残差
- 高度变化
- 已观察路线边
- 最近建筑与 DoorUID
- Terrain / Building 资源失败列表
- Player/NPC renderer mode
- FPS 与场景加载时间

这比只发截图更适合继续定位“坐标问题 / ROM 资源问题 / Actor 映射问题 / renderer 问题”。

## 重型逆向功能

正常运行默认关闭。需要时可显式开启：

```bat
set BLACK2_ENABLE_LEGACY_MAP_CACHE=1
```

这会恢复 legacy NativeMap 的周期性 BMD0/BTX0 Main-RAM 扫描。

旧二维自动移动实验需要显式：

```bat
set BLACK2_ENABLE_LEGACY_NAVIGATION=1
```

不建议用于桥、楼梯或多层地图。

## 证据原则

- RAM 当前事实优先。
- ROM 描述原版静态世界，不负责猜当前状态。
- `candidate` 不自动升级成 `verified`。
- UI fallback 只表示“为了看得见的占位”，不冒充原版贴图或模型。
- 不写游戏内存，除非用户主动使用现有输入控制接口；世界解析本身只读。

更多内容见 `docs/WORLD_LAB_V8_CN.md` 与 `docs/CALIBRATION_PROTOCOL_CN.md`。
