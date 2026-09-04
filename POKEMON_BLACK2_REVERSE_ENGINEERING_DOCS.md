# Pokémon Black 2 - 动态世界运行时与 ROM 逆向工程权威文档

> **最高原则**：`Current Game RAM + Current Loaded Game Resources + ROM Static Resources = Game Truth`

---

## 1. 核心架构与数据流

```text
Pokémon Black 2 (NDS Core)
        │
        ▼
BizHawk Emulator (MelonDS)
        │
        ▼
Lua Bridge (TCP Socket :8766)
        │
        ▼
Raw Memory API (ARM9 Main RAM 4MB / 0x02000000 ~ 0x02400000)
        │
        ▼
Runtime Object Resolver & SWAN Schema
        │
        ├── Player / Actor Parser (FieldActor @ 0x0223DE00)
        ├── Dialogue & Text Printer Parser (MsgBuffer @ 0x022490A0, Printer @ 0x0231FCB0)
        ├── Map Matrix & Chunk Parser (Matrix #0, #45 ... from NARC a/0/0/9)
        ├── 3D Visual Geometry & Texture Engine (BMD0 a/0/0/8 + BTX0 a/0/1/4)
        └── Overworld Entity System (NPCs, Furniture, Items, Warps, Triggers a/1/2/6)
        │
        ▼
World Semantic API (:8765 /api/v1/...)
        │
        ▼
AI Agent & Native Map Viewer
```

---

## 2. Verified SWAN RAM 运行时地址表 (ARM9 System Bus)

所有地址均在实机环境中经过动作实验、切图实验与内存差分（Memory Diff）严格验证。

### 2.1 玩家实体结构体 (`Player FieldActor`)
* **基准指针 / 数组首地址**：`0x0223DE00`
* **实体跨度 (Actor Stride)**：`0x100` 字节（Actor #0 = 玩家，Actor #1..#15 = 现场活跃 NPC）

| 字段名 | 偏移量 (Offset) | 数据类型 | 验证数值 / 范围 | 语义解释 | 验证状态 |
| :--- | :---: | :---: | :---: | :--- | :---: |
| `ModelID` | `+0x00` | `u16` | `2` (男主角 zero) | 角色 3D/2D 模型编号 | **Verified** |
| `ActorUID` | `+0x02` | `u16` | `2` | 实体唯一运行时 UID | **Verified** |
| `FaceDir` | `+0x0A` | `u16` | `0`=北, `1`=南, `2`=西, `3`=东 | 角色实时面朝方向 | **Verified** |
| `TargetGridX` | `+0x14` | `u32` | `0 ~ 4095` | 目标/逻辑 X 坐标 | **Verified** |
| `Elevation` | `+0x18` | `u16` | `11` (室内), `12` (室外) | 物理高程 / 高度层 (Z) | **Verified** |
| `TargetGridY` | `+0x1C` | `u32` | `0 ~ 4095` | 目标/逻辑 Y 坐标 | **Verified** |
| `GPos.X` (Current) | `+0x20` | `u32` | `0 ~ 4095` | 玩家当前网格 X 坐标 (Grid X) | **Verified** |
| `GPos.Y` (Current) | `+0x24` | `u32` | `0 ~ 4095` | 玩家当前网格 Y 坐标 (Grid Y) | **Verified** |
| `WPos.X` (Subpixel) | `+0x28` | `16.16 Fixed` | 移动时连续变化 | 世界浮点/子像素 X 坐标 | **Verified** |
| `WPos.Y` (Subpixel) | `+0x30` | `16.16 Fixed` | 移动时连续变化 | 世界浮点/子像素 Y 坐标 | **Verified** |

### 2.2 实时对话与打字机引擎 (`TextControlBlock & MsgBuffer & PixelData`)
* **打字控制块 (TCBL / TextControlBlock)**：`0x02332C20` (本次运行观察基址，生命周期中属堆分配实例)
* **打字机管理器候选结构体**：`0x0231FCB0`
* **活动消息文本缓冲区**：`0x022490A0` ~ `0x02249800` (StrBuf 头 12 字节，字符数组位于 `+0x0C: 0x022490AC`)
* **对话窗口光栅化缓冲区 (PixelData)**：`0x023353C0` ~ `0x023362BF` (240×32 像素, 4bpp, 3840 字节，Authoritative Raster-State Oracle)
* **对话活动硬件标志**：`0x02247546` (`1`=对话中, `0`=空闲自由移动)

> **证据角色说明 (Evidence Triangulation)**：
> 1. **字符身份事实 (Character Identity Truth)**：来自展开后的 `StrBuf` 文本流与受控时序验证的字符消费事件。
> 2. **空间可见性事实 (Spatial Visibility Truth)**：由底层光栅表面 `PixelData` 裁决行占位与逐帧位移，但不做 OCR 语义推断。
> 3. **状态转移事实 (State-Transition Truth)**：由 `Phase`、`SourceCursor`、`ScrollDistance` 协同状态机闭环。
> *注：当前重建路径证明了无需依赖专门的 `active_chars_on_screen` 或 `topLineIndex` 单一字段即可精确恢复可见文字；但其在引擎其他位置的存留状态仍保持 **Unresolved**。

| 字段名 | 物理地址 | 数据类型 | 验证数值 | 语义解释 | 验证状态 |
| :--- | :---: | :---: | :---: | :--- | :---: |
| `hw_dialogue_active` | `0x02247546` | `u8` | `1` / `0` | 硬件级脚本与消息框活动标志 | **Verified** |
| `printer_phase` | `0x02332C38` | `u32` | `0`=出字, `1`=屏间等待, `2`=末尾等待 | 实时打印机状态机阶段 (Phase) | **Verified (Tested Path)** |
| `first_page_latch` | `0x02332C3C` | `u32` | `1`=首屏, `0`=次屏+ | 首屏清屏锁定标志位 | **Verified (Tested Path)** |
| `source_cursor` | `0x02332C4C` | `u32` | `0x022490XX` | 当前字符消费/待读源指针 (精确地址) | **Verified (Tested Path)** |
| `scroll_distance_px`| `0x02332C54` | `u16` | `0` ~ `16` | 滚屏剩余像素（每步 4px 平滑上移） | **Verified (Tested Path)** |
| `pixel_data_oracle` | `0x023353C0` | `4bpp Bitmap` | 3840 字节 | 窗口光栅化底层 Authoritative Raster Oracle | **Verified** |
| `msg_buffer_base` | `0x022490A0` | `StrBuf` | 包含控制符 | Gen 5 展开后文本流 (+0x0C 为首字) | **Verified** |

### 2.3 玩家行进姿态 (`PlayerExState`)
* **地址**：`0x0221F000` (`u8`)
  * `0x0` = 徒步 (Walk / Run)
  * `0x1` = 骑行 (Cycling / Bike)
  * `0x2` = 水面冲浪 (Surf)
  * `0x3` = 潜水 (Dive)

---

## 3. ROM 静态资源 NARC 档案系统架构

| NARC 路径 | 文件总数 | 格式类型 | 资源说明 |
| :--- | :---: | :---: | :--- |
| **`a/0/0/8`** | 1065 | `BMD0` (NDS Nitro 3D) | 全合众地区 3D 地形与建筑模型库 |
| **`a/0/1/4`** | 560 | `BTX0` (NDS Nitro Texture) | 官方 3D 贴图与材质调色板库 |
| **`a/0/0/9`** | 416 | `Binary Matrix` | 宏观地图空间矩阵 (定义 Chunk 排布与尺寸) |
| **`a/0/1/2`** | 616 | `ZoneData Records` (48B/条) | 区域地图逻辑头 (关联 Matrix、音乐与事件) |
| **`a/1/2/6`** | 616 | `Overworld Events` | 空间实体表 (家具/道具 2918, NPC 3145, 门 1089, Trigger 575) |

---

## 4. 地图空间对齐与切图机制

### 4.1 室内场景 vs 室外大地图
1. **室内小房间 (如 Matrix #45 / Header #426)**：
   * 尺寸为 `1 × 1` 单块 Chunk (32×32 Tiles)。
   * 3D 模型：`Model #847 (m_h02_00_00.bmd0)` + `Texture #282 (btx0)`。
   * 采用包围盒局部投影（Mesh Bounding Box Projection）实现视口居中与精准对齐。
2. **室外城镇与道路 (如 Matrix #0 全球大矩阵)**：
   * 尺寸为 `29 × 27` Chunks。
   * 玩家位于 `(X=43, Y=760)`，属于 `Chunk (1, 23)`。
   * 系统自动将周边连通的 **23 个连续官方 3D 街区**（涵盖桧扇市、19号道路、算木镇）无缝拼接成宏观大地图。

### 4.2 空间可交互实体坐标对照表 (桧扇市区域)

| 实体名称 | 类别 | 世界坐标 (X, Y) | 3D 呈现标记 | 交互作用 |
| :--- | :---: | :---: | :--- | :--- |
| **🔴 散落道具球 (PokeBall)** | 道具球 | `(48, 758)` | 红白球体 + 发光光环 | 拾取道具/伤药 |
| **🪨 怪力巨石 (Boulder)** | 地形障碍 | `(22, 712)` | 棕褐色多面体岩石 | 秘传技“怪力”推动 |
| **🪵 居合斩树木 (Cut Tree)** | 地形障碍 | `(54, 720)` | 树干 + 绿色树冠 | 秘传技“居合斩”砍除 |
| **🪧 桧扇市路牌** | 地标指引 | `(44, 745)` | 蓝色指示标牌 | 弹出区域提示 |
| **🏠 主角家大门 (Player House)** | 空间传送门 | `(43, 761)` | 琥珀金传送门框 | 进出主角家 1F |
| **🏠 劲敌家大门 (Hugh's House)** | 空间传送门 | `(49, 739)` | 琥珀金传送门框 | 进出劲敌家 |
| **🏫 训练家学校正门** | 空间传送门 | `(38, 718)` | 琥珀金传送门框 | 进出学校与道馆 |
| **⛩️ 19号道路 警卫门廊** | 区域通道 | `(64, 708)` | 琥珀金传送门框 | 前往19号道路与算木镇 |
| **🧍 白露 (Bianca / 展望台)** | 剧情主线 NPC | `(16, 705)` | 粉色发光 NPC 模型 | 领取初始宝可梦与图鉴 |
| **🧍 劲敌 修 (Hugh)** | 剧情主线 NPC | `(46, 736)` | 蓝色发光 NPC 模型 | 触发首次对战 |

---

## 5. API 规范与端点速查

* **综合控制台 Web 页面**：`http://127.0.0.1:8765/`
* **全景运行时物理真理快照工作台**：`http://127.0.0.1:8765/ram-dumper`
* **对话与打字机实时检查器**：`http://127.0.0.1:8765/dialogue-inspector`
* **ROM 原生 3D 官方地图 Viewer**：`http://127.0.0.1:8765/frontend/native-map.html`
* **实时玩家坐标与地图状态**：`GET /api/v1/map/current`
* **实时 3D 模型网格与材质流**：`GET /api/v1/map/visual`
* **实时文字化地图知识库**：`GET /api/v1/map/knowledge/current.txt`
* **全量 ROM 目录索引**：`GET /api/v1/map/knowledge/catalog.txt`
* **实时对话与时序流**：`GET /api/observer/presentation`
* **全域硬件内存原子导出**：`POST /api/dev/dump_full_ram`
* **快照清册与一键 ZIP**：`GET /api/dev/dumps`
* **运行时 Field 对象解析**：`GET /api/v1/map/runtime/field`
* **物理真理与 ROM 对齐地图**：`GET /api/v1/map/truth/current`

---

## 6. 版本里程碑与发布历史 (Milestone Releases)

### `v2.1.0` - Map Truth v2 & Full Non-ROM Hardware Memory Exporter (2026-09-04)
* **Map Truth v2 闭环落地**：
  - 新增 `GET /api/v1/map/runtime/field`：从当前 Main RAM 动态解析 Field -> Player -> PlayerActor -> ActorSystem -> Mapper -> Loaded Chunks -> Props -> DoorUID -> TileType。
  - 新增 `GET /api/v1/map/truth/current`：将运行时 RAM 与 ROM Matrix (`a/0/0/9`)、Map Header、BMD0 (`a/0/0/8`)、BTX0 (`a/0/1/4`)、NPC Spawn、Warp、Trigger、Permission 进行物理匹配闭环。
* **通用全域硬件导出优化**：
  - Lua Bridge `memory.read` 支持高速 binary-string / chunked-array 批量流式传输，解决大范围 Main RAM 遍历超时。
  - `memory.read_bytes` 支持定制超时间隔（`timeout=10.0s`），支撑 4MB 全量 RAM 零丢失读取。
  - 增强 `ram-dumper.html`：包含全部内存域物理文件验证、CRC 校验、ZIP 一键安全下载。

### `v2.0.0` - Universal Ground-Truth Runtime & Multi-Domain Dumper (2026-09-04)
* **全域硬件内存原子导出器 (Universal Dumper)**：支持一次 RPC 并行落盘 Main RAM (4MB)、ITCM (32KB)、DTCM (16KB)、Shared WRAM (32KB)、ARM7 WRAM (64KB)、SRAM (512KB)、Native 画面截图 (PNG)、ARM9 完整寄存器组 (PC/SP/LR/CPSR/R0-R12) 与结构化 `manifest.json`，自动生成一键打包 ZIP。
* **Root Tuple 动态解析链 (DynamicDialogueResolver)**：彻底摆脱对固定堆地址（`0x02332C20` / `0x0232B400`）的依赖，通过 `MsgBGSys + 0x15C` 动态定位 `talkmsgwin` 并匹配 `[TCBL_Phase, BmpWin, Context, StrBuf]` 四指针元组，实现跨 NPC、跨堆重分配的自适应解析。
* **流式 Token 解释器 (VisibleTextLedger)**：实现了真正的单帧零历史依赖解析（Snapshot Independence / EXP-021），精准闭环 `CLEAR` 翻页清屏、`LF` 换行、`SCROLL` 逐像素平移、`EOS` 终止，彻底消除下一屏泄露与旧行残留。
* **物理栅格裁判 (Authoritative Raster Oracle)**：以 `PixelData`（240×32 4bpp 3840B）为物理栅格占位真理，与 `StrBuf` 字符流、`Phase/Cursor` 状态机形成三方闭环验证。
* **全景运行时快照工作台 (ram-dumper.html)**：提供 7 大场景预设打标（NPC对话、主角坐标、路牌、进出建筑、战斗、菜单、异常排查），带有多维物理态雷达与历史快照浏览器。
* **Lua Bridge 升级至 `v1.4.0-universal-dump`**：支持 `memory.dump_universal` 与原生文件写入。
