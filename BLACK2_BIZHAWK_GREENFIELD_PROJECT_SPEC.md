# Pokémon Black 2 AI Semantic Runtime
# 全新项目设计规范 v1.0
# BizHawk Greenfield Architecture

> 本文档用于交给 Codex、Antigravity、Hermes 或其他代码 Agent，直接从零创建项目。
>
> 不讨论旧项目，不讨论迁移，不兼容任何旧目录结构。
>
> 第一目标是先可靠观察、检查并控制正在运行的 BizHawk。
>
> 最终目标是让 AI 不依赖截图、OCR 或画面颜色识别，通过 BizHawk 内部 RAM、ROM 静态资源和游戏内部状态，完整理解并完成《宝可梦 黑2》主线。

---

# 1. 项目名称

建议：

```text
black2-semantic-runtime
```

产品含义：

```text
Pokémon Black 2
+
BizHawk Runtime
+
Semantic World Model
+
AI Control API
```

不要把项目命名为：

```text
ram-reader
pokemon-bot
screen-agent
```

因为最终系统远远超过单纯读 RAM 或自动按键。

---

# 2. 最终目标

最终必须做到：

```text
用户打开 Pokémon Black 2
        ↓
BizHawk Bridge 连接
        ↓
Runtime 自动确认 BizHawk / ROM / RAM
        ↓
Runtime 读取游戏内部状态
        ↓
ROM Database 提供静态地图 / 文本 / 脚本 / NPC
        ↓
Semantic State Engine 合并实时 RAM 与 ROM
        ↓
AI 获得结构化游戏世界
        ↓
AI 通过高层 API 操作
        ↓
Runtime 自己完成移动、菜单和输入细节
        ↓
AI 从 New Game 一直推进到 Hall of Fame
```

运行期间：

```text
截图
OCR
模板匹配
颜色判断
视觉大模型
```

都不能成为正式 Runtime 的状态来源。

可以在 Web UI 中保留 BizHawk 画面给人观察。

但 Human Preview 不允许进入 State Engine。

---

# 3. 项目最重要的原则

## 3.1 BizHawk 是唯一模拟器接口

Runtime 不读取 Windows 进程内存。

禁止：

```text
ReadProcessMemory
OpenProcess memory scan
寻找 melonDS host RAM base
依赖 HWND 获取游戏状态
对 EmuHawk.exe 做进程内存逆向
```

允许通过 OS 查询：

```text
EmuHawk.exe 是否运行
PID
可执行文件路径
启动参数
进程存活状态
```

这些只用于健康检查。

真正的游戏 RAM 必须来自 BizHawk Lua API。

---

## 3.2 ROM 与 RAM 分工

RAM 回答：

```text
现在发生了什么
```

ROM 回答：

```text
这个世界本来是什么
```

例如：

```text
ROM:
NPC #7 的 script_id = 128
NPC 初始位置 = ...
Warp #3 通往 Map #42

RAM:
NPC #7 当前已经出现
NPC #7 现在站在 x=13 y=22
玩家正面向 NPC #7
```

两者合并：

```text
Semantic World State
```

---

## 3.3 AI 不直接处理地址

正式 AI 不应该看到：

```text
0x02123456
```

正式 AI 应看到：

```json
{
  "player": {
    "x": 210,
    "y": 659,
    "facing": "west"
  }
}
```

RAM 地址只存在于：

```text
Profile
Resolver
Research UI
Developer MCP
```

---

## 3.4 所有版本差异都进入 Profile

至少支持：

```text
Black 2 中文补丁版
Black 2 USA
```

未来可以增加：

```text
JP
EU
其他汉化补丁
```

Runtime Decoder 不因为语言版本复制一份。

---

# 4. 推荐技术栈

## Backend

```text
Python 3.12+
FastAPI
Uvicorn
Pydantic
asyncio
WebSocket
SQLite
```

## Frontend

```text
TypeScript
React
Vite
Three.js
React Three Fiber
TanStack Query
Zustand 或类似轻量状态管理
```

## BizHawk

```text
BizHawk 2.11.x 或当前稳定版
NDS core: melonDS
Lua Bridge
```

## 数据

```text
SQLite:
profile metadata
ROM metadata
symbol metadata
research experiments
story index

Files:
ROM extraction cache
GLB
BTX/BMD source
RAM fixtures
large binary snapshots
```

---

# 5. 全新目录结构

必须从一开始保持清晰。

```text
black2-semantic-runtime/
│
├─ README.md
├─ pyproject.toml
├─ package.json
│
├─ config/
│  ├─ app.toml
│  └─ logging.toml
│
├─ bridge/
│  └─ bizhawk/
│     ├─ black2_bridge.lua
│     ├─ probe.lua
│     ├─ protocol.md
│     └─ README.md
│
├─ backend/
│  └─ black2/
│     │
│     ├─ app.py
│     │
│     ├─ bizhawk/
│     │  ├─ process_probe.py
│     │  ├─ transport.py
│     │  ├─ socket_transport.py
│     │  ├─ http_attach_transport.py
│     │  ├─ bridge_client.py
│     │  ├─ capabilities.py
│     │  ├─ doctor.py
│     │  └─ session.py
│     │
│     ├─ profile/
│     │  ├─ model.py
│     │  ├─ loader.py
│     │  ├─ resolver.py
│     │  ├─ validator.py
│     │  └─ identity.py
│     │
│     ├─ memory/
│     │  ├─ domains.py
│     │  ├─ reader.py
│     │  ├─ sampler.py
│     │  ├─ snapshot.py
│     │  ├─ diff.py
│     │  ├─ scanner.py
│     │  ├─ correlation.py
│     │  └─ watch.py
│     │
│     ├─ decoders/
│     │  ├─ player.py
│     │  ├─ field.py
│     │  ├─ field_objects.py
│     │  ├─ trainer.py
│     │  ├─ party.py
│     │  ├─ pokemon.py
│     │  ├─ inventory.py
│     │  ├─ ui.py
│     │  ├─ dialogue.py
│     │  ├─ script_context.py
│     │  ├─ flags.py
│     │  └─ battle.py
│     │
│     ├─ state/
│     │  ├─ models.py
│     │  ├─ store.py
│     │  ├─ fusion.py
│     │  ├─ provenance.py
│     │  ├─ versions.py
│     │  └─ events.py
│     │
│     ├─ rom/
│     │  ├─ nds.py
│     │  ├─ narc.py
│     │  ├─ text.py
│     │  ├─ script.py
│     │  ├─ map_matrix.py
│     │  ├─ map_definition.py
│     │  ├─ map_model.py
│     │  ├─ permissions.py
│     │  ├─ overworld_events.py
│     │  └─ encounters.py
│     │
│     ├─ world/
│     │  ├─ models.py
│     │  ├─ coordinates.py
│     │  ├─ map_database.py
│     │  ├─ visual_assets.py
│     │  ├─ collision.py
│     │  ├─ navgrid.py
│     │  ├─ occupancy.py
│     │  ├─ entities.py
│     │  ├─ warps.py
│     │  ├─ world_graph.py
│     │  └─ planner.py
│     │
│     ├─ story/
│     │  ├─ script_ir.py
│     │  ├─ graph.py
│     │  ├─ objectives.py
│     │  ├─ resolver.py
│     │  └─ database.py
│     │
│     ├─ actions/
│     │  ├─ models.py
│     │  ├─ engine.py
│     │  ├─ input.py
│     │  ├─ navigation.py
│     │  ├─ interaction.py
│     │  ├─ dialogue.py
│     │  ├─ menu.py
│     │  ├─ battle.py
│     │  └─ recovery.py
│     │
│     ├─ api/
│     │  ├─ rest.py
│     │  ├─ websocket.py
│     │  └─ schemas.py
│     │
│     ├─ mcp/
│     │  ├─ runtime_server.py
│     │  └─ research_server.py
│     │
│     └─ research/
│        ├─ experiments.py
│        ├─ findings.py
│        ├─ candidates.py
│        └─ reports.py
│
├─ frontend/
│  └─ src/
│     ├─ app/
│     ├─ pages/
│     │  ├─ Overview/
│     │  ├─ BizHawkInspector/
│     │  ├─ World/
│     │  ├─ Runtime/
│     │  ├─ Story/
│     │  ├─ Battle/
│     │  ├─ Party/
│     │  ├─ MemoryResearch/
│     │  ├─ ProfileLab/
│     │  └─ Logs/
│     ├─ components/
│     ├─ world3d/
│     └─ api/
│
├─ profiles/
│  ├─ black2_cn.json
│  └─ black2_us.json
│
├─ romdata/
│
├─ research/
│  ├─ snapshots/
│  ├─ experiments/
│  ├─ findings/
│  └─ reports/
│
└─ tests/
   ├─ bridge/
   ├─ memory/
   ├─ profiles/
   ├─ decoders/
   ├─ world/
   ├─ story/
   ├─ actions/
   └─ e2e/
```

---

# 6. AI 开工时第一件事不是找游戏地址

第一步必须先建立：

```text
BizHawk Inspector
```

必须先证明：

```text
AI 能看到 BizHawk
AI 知道 BizHawk 当前加载了什么
AI 能列出 RAM domains
AI 能稳定读取 RAM
AI 能稳定发送输入
```

这些成立以后再开始 Pokémon Black 2 逆向。

---

# 7. 如何“看到”用户正在运行的 BizHawk

这里的“看到”不是截图。

定义四层检测。

---

# 8. Layer 0：Process Probe

后端先做操作系统级健康检查。

Windows：

```text
查找进程名 EmuHawk.exe
```

记录：

```json
{
  "running": true,
  "pid": 12345,
  "exe": "C:\\BizHawk\\EmuHawk.exe",
  "cmdline": ["..."],
  "started_at": "..."
}
```

用途只有：

```text
BizHawk 是否运行
运行的是哪个 executable
是不是有多个实例
是否由本项目启动
```

禁止从 PID 读取游戏 RAM。

如果有多个 BizHawk：

```text
session 必须要求具体选择一个 bridge
```

不能只按 PID 猜哪个是游戏。

---

# 9. Layer 1：Bridge Probe

真正判断“这个 BizHawk 是否可供 Runtime 使用”的标准：

```text
Lua Bridge 是否连通
```

后端：

```text
GET /api/bizhawk/status
```

返回：

```json
{
  "process": "running",
  "bridge": "connected",
  "session_id": "...",
  "last_heartbeat_ms": 23
}
```

只有：

```text
bridge == connected
```

才认为可以读游戏状态。

---

# 10. 已经运行的 BizHawk 如何 Attach

用户可能已经手动启动了 BizHawk。

不能要求每次都关掉重开。

支持：

```text
Attach Mode
```

步骤：

1. 后端启动 Attach HTTP endpoint。
2. 用户打开 BizHawk。
3. `Tools -> Lua Console`
4. 打开：

```text
bridge/bizhawk/black2_bridge.lua
```

BizHawk 官方支持从 Lua Console 加载脚本。

Bridge 启动后：

```text
comm.httpPost(...)
```

向后端发送 hello。

后端生成：

```text
session_id
```

然后 Bridge 每隔少量帧：

```text
HTTP poll command
HTTP send result
heartbeat
```

Attach Mode 主要用于：

```text
检查当前 BizHawk
ROM identity
Memory Domain discovery
RAM research
低频控制
```

这样即使用户已经先打开 BizHawk，也能接入。

---

# 11. Managed Mode

正式长期运行推荐：

```text
Managed Mode
```

后端先监听 TCP。

然后启动：

```powershell
EmuHawk.exe `
  --socket-ip=127.0.0.1 `
  --socket-port=8766 `
  --lua=C:\project\bridge\bizhawk\black2_bridge.lua `
  C:\roms\PokemonBlack2.nds
```

BizHawk 当前官方 CLI 支持：

```text
--socket-ip
--socket-port
--lua
ROM path
```

这种模式优势：

```text
更低延迟
高频 state streaming
更适合 battle / movement
Runtime 可完整管理 session
```

---

# 12. 两种模式统一协议

上层不得知道当前是：

```text
socket
HTTP
```

定义：

```python
class BizHawkTransport:
    async def request(...)
    async def send(...)
    async def recv(...)
    async def health(...)
```

实现：

```text
SocketTransport
HttpAttachTransport
```

以后可以增加：

```text
MMFTransport
```

---

# 13. Bridge 启动必须主动报告什么

Bridge 一启动，不要立即读 Pokémon 地址。

先发送：

```json
{
  "type": "hello",
  "bridge_version": "1.0.0",
  "bizhawk": {},
  "game": {},
  "memory": {},
  "events": {},
  "capabilities": {}
}
```

---

# 14. BizHawk Identity

Bridge 必须调用：

```lua
client.getversion()
emu.getsystemid()
```

返回：

```json
{
  "bizhawk_version": "2.11.1",
  "system_id": "NDS"
}
```

如果：

```text
system_id != NDS
```

Runtime 必须停止 Black 2 decoder。

---

# 15. ROM Identity

Bridge 必须调用：

```lua
gameinfo.getromname()
gameinfo.getromhash()
gameinfo.getstatus()
gameinfo.indatabase()
```

返回：

```json
{
  "rom_name": "...",
  "rom_hash": "...",
  "database_status": "Unknown",
  "in_database": false
}
```

中文补丁 ROM 很可能：

```text
Unknown
Hack
NotInDatabase
```

这本身不是错误。

真正 Profile 绑定必须使用：

```text
ROM hash
```

不要用窗口标题。

---

# 16. Emulator State

Bridge 报告：

```lua
emu.framecount()
client.ispaused()
client.isturbo()
```

输出：

```json
{
  "frame": 122338,
  "paused": false,
  "turbo": false
}
```

heartbeat 每次必须带：

```text
frame
```

如果：

```text
wall clock 在走
frame 长时间不变
paused == false
```

判为异常。

---

# 17. Memory Domain Discovery

这是最重要的 BizHawk 检查之一。

Bridge 调用：

```lua
memory.getmemorydomainlist()
memory.getcurrentmemorydomain()
```

然后对每个 domain：

```lua
memory.getmemorydomainsize(domain)
```

返回：

```json
{
  "current_domain": "...",
  "domains": [
    {
      "name": "...",
      "size": 4194304
    }
  ]
}
```

禁止代码写死：

```text
Main RAM
System Bus
```

因为 Memory Domain 名称由当前 core 暴露。

永远以运行时枚举结果为准。

---

# 18. Domain Inspector 页面

Web UI 新建：

```text
BizHawk Inspector
```

其中 Memory Domains 表：

| Domain | Size | Readable | Hash Test | Candidate |
|---|---:|---|---|---|
| ... | ... | yes | pass | main RAM |
| ... | ... | yes | pass | bus |

点开 domain 可以：

```text
Read Offset
Dump small range
Hash range
Open research experiment
```

---

# 19. Memory Domain 自检

每个 domain 自动执行：

```text
size > 0
read first 16 bytes
read last valid 16 bytes
hash small region
```

不能：

```text
直接读取 domain size 以外
```

结果：

```json
{
  "domain": "...",
  "size_ok": true,
  "read_start_ok": true,
  "read_end_ok": true,
  "hash_ok": true
}
```

---

# 20. RAM 大块读取

使用：

```lua
memory.read_bytes_as_binary_string(addr, length, domain)
```

不要使用：

```text
一字节一字节 Python RPC
```

例如要读取 4 MB：

```text
一次或分 64 KB / 256 KB chunk
```

研究模式可以完整 dump。

正常 Runtime 不每帧 dump 4 MB。

---

# 21. Memory Hash

BizHawk 提供：

```lua
memory.hash_region(...)
```

用于：

```text
快速判断区域有没有变化
避免不必要传输
```

例如：

```text
party block hash 没变
```

就不需要重新发送整块 party。

---

# 22. Bridge Capability Discovery

Bridge 不要假设所有函数存在。

启动时调用：

```lua
comm.getluafunctionslist()
```

或者在 Lua 中检查函数是否存在。

生成 capability：

```json
{
  "binary_memory_read": true,
  "memory_hash": true,
  "socket": true,
  "http": true,
  "mmf": true,
  "bus_callbacks": true,
  "register_read": true
}
```

---

# 23. Bus Event Scope Discovery

调用：

```lua
event.availableScopes()
```

保存：

```json
{
  "event_scopes": [...]
}
```

以后使用：

```text
on_bus_write
on_bus_read
on_bus_exec
```

时必须指定有效 scope。

---

# 24. 不允许一开始监听所有 memory write

虽然 BizHawk 支持：

```lua
event.on_bus_write(callback, nil)
```

但是不能这么做。

整个总线 write hook：

```text
可能极度影响性能
```

正确流程：

1. snapshot diff 缩小地址范围
2. 得到少量候选
3. 对候选地址注册 callback
4. 观察写入
5. 取消 callback

---

# 25. BizHawk Doctor

后端必须实现 CLI：

```text
black2 doctor
```

以及 API：

```text
GET /api/bizhawk/doctor
```

---

# 26. Doctor 检查等级

## D0 Process

```text
EmuHawk.exe 是否存在
```

## D1 Bridge

```text
Bridge heartbeat 是否正常
```

## D2 Identity

```text
BizHawk version
system == NDS
ROM hash
```

## D3 Memory

```text
domains 可枚举
candidate RAM domain 可读
```

## D4 Timing

```text
frame 在正常变化
同一请求 frame 信息可信
```

## D5 Input

```text
button probe 可发送
```

## D6 Profile

```text
当前 ROM 是否匹配某 Profile
```

## D7 Semantic

```text
至少 player/map/party decoder 正常
```

最终显示：

```json
{
  "status": "READY",
  "checks": {
    "process": "PASS",
    "bridge": "PASS",
    "system": "PASS",
    "rom": "PASS",
    "memory": "PASS",
    "input": "PASS",
    "profile": "PASS"
  }
}
```

---

# 27. Doctor 状态定义

统一：

```text
NO_PROCESS
PROCESS_ONLY
BRIDGE_CONNECTING
BRIDGE_CONNECTED
WRONG_SYSTEM
NO_ROM
UNKNOWN_ROM
MEMORY_UNAVAILABLE
INPUT_UNAVAILABLE
PROFILE_MISSING
SEMANTIC_PARTIAL
READY
ERROR
```

前端必须直接显示这个状态。

---

# 28. BizHawk Inspector UI

页面布局：

```text
┌────────────────────────────────────────────────────────────┐
│ BizHawk Inspector                              READY       │
├──────────────────────────────┬─────────────────────────────┤
│ Emulator                     │ Session                     │
│ Version                      │ Session ID                  │
│ System                       │ Transport                   │
│ Paused                       │ Last heartbeat              │
│ Frame                        │ Latency                     │
│ Turbo                        │ Packets                     │
├──────────────────────────────┴─────────────────────────────┤
│ ROM                                                        │
│ Name / Hash / DB Status / Profile                          │
├────────────────────────────────────────────────────────────┤
│ Memory Domains                                             │
│ Domain | Size | Read | Hash | Candidate                    │
├────────────────────────────────────────────────────────────┤
│ Capabilities                                               │
├────────────────────────────────────────────────────────────┤
│ Tests                                                      │
│ [Run Doctor] [RAM Probe] [Input Probe] [Create Snapshot]   │
└────────────────────────────────────────────────────────────┘
```

---

# 29. 如何检查输入

BizHawk Lua 支持：

```lua
joypad.get()
joypad.getimmediate()
joypad.set(...)
```

项目必须先获取当前 core 的控制名称。

不要只假设：

```text
A
B
Up
Down
```

虽然 NDS 通常如此。

Research 页面要显示：

```text
当前 virtual controls
当前 immediate host input
Runtime injected input
```

避免人类键盘与 AI 同时输入时产生误判。

---

# 30. NDS Touch

触屏必须单独测试。

BizHawk NDS 使用 analog：

```text
Touch X
Touch Y
```

并需要 touch press 状态。

实现 `TouchProbe`。

测试：

```text
center
top-left
bottom-right
release
```

每一步都必须用 BizHawk input display 或内部 input state 验证。

注意当前 BizHawk 2.11.x 存在一个已知问题：

```text
默认 mouse binding 可能与 joypad.setanalog 的 Touch X/Y 冲突
```

因此项目 Doctor 必须增加：

```text
touch_conflict_warning
```

如果触屏自动化异常，第一检查项：

```text
Config -> Controllers
取消 Touch X / Touch Y 的鼠标轴绑定
```

不能因为 touch 失败就回退截图点击。

---

# 31. Bridge Protocol

所有消息统一 envelope：

```json
{
  "v": 1,
  "id": "req_123",
  "type": "request",
  "op": "memory.read",
  "payload": {},
  "frame_hint": null
}
```

response：

```json
{
  "v": 1,
  "id": "req_123",
  "type": "response",
  "ok": true,
  "frame": 123456,
  "payload": {}
}
```

event：

```json
{
  "v": 1,
  "type": "event",
  "event": "heartbeat",
  "frame": 123456,
  "payload": {}
}
```

---

# 32. Socket framing

BizHawk `comm.socketServerResponse()` 当前要求远端响应使用长度前缀。

Python -> BizHawk：

```text
<decimal length><space><payload>
```

例如：

```text
7 {"a":1}
```

必须把这个 framing 封装在：

```text
SocketTransport
```

其他业务模块绝不能处理这个格式。

---

# 33. Bridge Commands

第一版至少支持：

```text
bridge.hello
bridge.capabilities
bridge.ping

emu.state
emu.pause
emu.resume
emu.frame_advance

game.info

memory.domains
memory.read
memory.read_batch
memory.hash
memory.dump

input.state
input.press
input.hold
input.release
input.touch

event.watch_write
event.unwatch

savestate.save
savestate.load
```

---

# 34. read_batch

最重要的 Runtime command。

例如：

```json
{
  "op": "memory.read_batch",
  "payload": {
    "ranges": [
      {
        "key": "player",
        "domain": "...",
        "addr": 1234,
        "length": 64
      },
      {
        "key": "party",
        "domain": "...",
        "addr": 5678,
        "length": 1416
      }
    ]
  }
}
```

同一个采样周期尽量一次读取。

---

# 35. Frame Consistency

read_batch response：

```json
{
  "frame_start": 1000,
  "frame_end": 1000,
  "atomic_frame": true
}
```

如果：

```text
frame_start != frame_end
```

则：

```text
atomic_frame=false
```

重要 decoder 可选择重读。

---

# 36. Runtime Sampling

正常运行不要发送整个 RAM。

按域拆：

## 高频 30~60 Hz

```text
player
movement
input lock
battle phase
UI controller
dialogue active
current script
```

## 中频 10~20 Hz

```text
runtime NPC
battle battlers
cursor
```

## 低频 1~5 Hz 或 hash change

```text
party
inventory
trainer
flags
```

## 事件触发

```text
map changed
party changed
battle started
```

立即刷新相关 block。

---

# 37. Profile Identity

每个 ROM Profile：

```json
{
  "id": "black2_cn_xxx",
  "game": "pokemon_black_2",
  "rom_hash": "...",
  "base_region": "...",
  "language": "zh-CN",
  "symbols": {}
}
```

启动：

```text
gameinfo.getromhash()
↓
profile lookup
```

没有 profile：

```text
进入 Research Mode
```

不能偷偷使用另一个 ROM 的地址。

---

# 38. Symbol Model

```json
{
  "field.player": {
    "resolver": {
      "type": "fixed",
      "address_space": "nds_bus",
      "address": "0x02......"
    },
    "domain_policy": "main_ram_or_bus",
    "size": 64,
    "decoder": "field_player_v1",
    "validator": "field_player_v1",
    "confidence": 1.0
  }
}
```

---

# 39. NDS 地址空间

Profile 内建议使用：

```text
NDS bus address
```

Main RAM：

```text
0x02000000 - 0x023FFFFF
```

但不要直接把这个范围当成 BizHawk domain offset。

建立：

```text
AddressTranslator
```

例如：

```text
System Bus:
bus address 原样

Main RAM:
bus address - 0x02000000
```

实际 domain 名称和可用性必须由 BizHawk Inspector 验证。

---

# 40. Reverse Engineering Lab

这是找到中文版本地址的主要系统。

不是散装脚本。

Web 页面：

```text
Memory Research
```

---

# 41. Snapshot

点击：

```text
Snapshot A
```

保存：

```text
domain
frame
full RAM 或 selected ranges
hash
experiment metadata
```

---

# 42. Controlled Experiment

定义实验：

```json
{
  "name": "find_player_x",
  "steps": [
    "snapshot A",
    "move right 1 tile",
    "snapshot B",
    "move right 1 tile",
    "snapshot C",
    "move left 1 tile",
    "snapshot D"
  ]
}
```

---

# 43. Diff

支持：

```text
changed
unchanged
increased
decreased
changed by exact amount
bit changed
range changed
```

---

# 44. Correlation

例如：

```text
A -> B +1
B -> C +1
C -> D -1
```

输出候选。

---

# 45. Value Search

支持：

```text
u8
s8
u16 LE
s16 LE
u32 LE
bitfield
byte sequence
```

---

# 46. Candidate Ranking

候选字段：

```text
address
type
score
matches
failed tests
nearby structure entropy
pointer likelihood
```

---

# 47. Write Watch

候选缩小后：

```text
event.on_bus_write
```

只监控：

```text
候选地址
或很小范围
```

记录：

```text
frame
address
value
scope
```

---

# 48. BizHawk 内置工具也要利用

Research UI 中提供快捷按钮：

```text
Open RAM Search
Open RAM Watch
Open Hex Editor
Open Trace Logger
```

BizHawk Lua 有：

```text
client.openramsearch
client.openramwatch
client.openhexeditor
client.opentracelogger
```

这些是研究工具。

不属于正式 Runtime。

---

# 49. 第一批必须逆向的 Runtime 状态

不要先研究所有内容。

按顺序：

```text
1 player position
2 facing
3 map
4 movement/busy
5 party
6 game mode
7 dialogue active
8 UI/menu context
9 field objects
10 battle context
```

---

# 50. Semantic State 总模型

```json
{
  "state_version": 10031,
  "emu_frame": 420133,
  "mode": "overworld",
  "system": {},
  "player": {},
  "world": {},
  "screens": {},
  "dialogue": {},
  "party": {},
  "inventory": {},
  "battle": null,
  "story": {},
  "action": {}
}
```

---

# 51. Provenance

每个关键字段能够追溯：

```json
{
  "value": 210,
  "source": "ram",
  "symbol": "field.player.x",
  "verified": true,
  "confidence": 1.0
}
```

对于推断：

```json
{
  "value": "virbank_gym",
  "source": "story_resolver",
  "evidence": [
    "flag:123=true",
    "map:42"
  ],
  "confidence": 0.96
}
```

---

# 52. 不允许假数据

没有数据：

```json
{
  "money": null
}
```

不能：

```text
为了 UI 好看写一个默认 10000
```

---

# 53. ROM Pipeline

完整地图和剧情不能只靠 RAM。

建立：

```text
ROM Extraction Pipeline
```

输入：

```text
.nds
```

输出：

```text
romdata/<rom_hash>/
```

---

# 54. ROM 数据层

解析：

```text
NDS filesystem
NARC
Text archives
Map Matrix
Map Definitions
BMD0
BTX0
Permissions
Overworld Events
Warps
Triggers
Scripts
Encounters
```

所有 parser 都需要 golden test。

---

# 55. World Model

3D 地图不是装饰。

World Model 是：

```text
AI 空间理解
+
3D Dashboard
+
路径规划
+
NPC
+
剧情目标
```

的共同数据源。

---

# 56. World Model 层级

```text
World
  Region
    MapDefinition
      MatrixCell
        VisualModel
        Collision
        Entities
        Triggers
        Warps
```

---

# 57. 坐标系统

必须分清：

```text
WorldTile
MatrixCell
ModelLocalTile
MapLocalTile
CollisionCell
EventCoordinate
Visual3DCoordinate
TouchScreenCoordinate
```

统一：

```text
CoordinateTransformRegistry
```

禁止各模块自己写转换公式。

---

# 58. Map Definition

```json
{
  "id": 42,
  "name_localized": "...",
  "name_canonical": "Virbank Complex",
  "matrix_id": 0,
  "cells": [],
  "bounds": {},
  "warps": [],
  "entities": [],
  "triggers": [],
  "collision": {}
}
```

---

# 59. 3D 资产

处理：

```text
BMD0
BTX0
```

转换成 Web 可显示：

```text
GLB
```

缓存：

```text
romdata/<rom_hash>/visual/models/
```

Viewer 不依赖当前 RAM 中恰好加载了模型。

---

# 60. 完整地图浏览

用户可以：

```text
搜索任意 Map
打开任意 Map
即使玩家不在那里
```

地图来自 ROM。

RAM 只叠加：

```text
当前玩家
当前 Runtime NPC
当前剧情状态
```

---

# 61. 3D World UI

页面：

```text
/world
```

布局：

```text
顶部:
Map Search
Current Map
Follow Player
Floor
Camera

中间:
3D View

右侧:
Inspector

底部:
AI Route
Events
```

---

# 62. 图层

支持：

```text
Visual Model
Raw Permission
Semantic Collision
NavGrid
Static NPC
Runtime NPC
Furniture
Warp
Trigger
Trainer Sight
Encounter
Story Target
AI Route
Coordinates
```

---

# 63. 点击 Tile

Inspector 显示：

```json
{
  "world_tile": {},
  "map_local": {},
  "collision": {},
  "raw_permissions": [],
  "entity": null,
  "warp": null,
  "trigger": null,
  "reachable": true
}
```

---

# 64. Collision

视觉模型不能用于寻路判断。

寻路依据：

```text
Permissions
Collision
Movement Rules
Dynamic Occupancy
```

---

# 65. NavCell

```json
{
  "x": 18,
  "y": 19,
  "walkable": true,
  "terrain": "normal",
  "height_layer": 0,
  "entry": ["north","south","east","west"],
  "exit": ["north","south","east","west"],
  "requirements": []
}
```

未知：

```text
semantic = unknown
```

不能猜。

---

# 66. 特殊地图规则

必须逐步支持：

```text
ledge
stairs
bridge
door
warp
surf
strength
cut
bike
escalator
elevator
script movement
one-way transition
```

这些都建成 graph edge。

---

# 67. Static Entity

ROM 解析：

```text
NPC Definition
Furniture
Warp
Trigger
Trainer
```

---

# 68. Runtime Entity

RAM 逆向：

```text
Field Object Manager
```

输出：

```json
{
  "runtime_id": 4,
  "definition_id": 7,
  "position": {},
  "facing": "east",
  "moving": false,
  "visible": true,
  "blocking": true,
  "interactable": true
}
```

---

# 69. Occupancy

```text
Static NavGrid
+
Runtime Entity Occupancy
=
Current NavGrid
```

NPC 移动以后重新更新。

---

# 70. World Graph

Node：

```text
MapDefinition
```

Edge：

```text
Warp
Door
Stairs between maps
Gate
Cave
Special Script Transition
Fly
```

---

# 71. Local Planner

A*。

State：

```text
x
y
height_layer
movement_mode
```

---

# 72. Route 不交给 LLM 执行

LLM 说：

```text
去某个 NPC
```

Runtime：

```text
World Graph
↓
Local A*
↓
按键执行
↓
逐步验证
```

LLM 不处理几百个 Up/Down。

---

# 73. 双屏 Semantic UI

两个屏幕也不读取像素。

最终：

```json
{
  "top": {
    "scene": "field"
  },
  "bottom": {
    "scene": "party_menu",
    "focus": {
      "type": "party_slot",
      "index": 2
    }
  }
}
```

---

# 74. UI 逆向对象

寻找：

```text
game mode
active controller
field controller
menu controller
message box
text printer
cursor
choice controller
battle command
party UI
bag UI
summary UI
shop UI
PC UI
```

---

# 75. UI 逆向方法

例如找 Party Cursor：

```text
baseline
↓
打开 Party
↓
snapshot
↓
cursor down
↓
snapshot
↓
cursor down
↓
snapshot
↓
cursor up
↓
snapshot
↓
correlation
↓
candidate
↓
write watch
↓
structure
```

不能截图判断选中的框。

---

# 76. ScreenState

```json
{
  "scene": "bag",
  "subscene": "items",
  "active": true,
  "cursor": {
    "index": 4
  },
  "scroll": 1,
  "choices": [],
  "accepting_input": true,
  "busy": false
}
```

---

# 77. Dialogue

读取：

```text
active
text bank
text id
current page
speaker
choice
cursor
awaiting input
```

优先从：

```text
script context
text printer
message controller
```

获取。

不 OCR。

---

# 78. Text Provider

ROM 文本统一：

```json
{
  "text_id": 42,
  "localized": "小磁怪",
  "canonical": "Magnemite"
}
```

所有 AI 决策优先使用：

```text
ID
```

---

# 79. Script Parser

把 Event Script 解码成 IR。

例如：

```json
{
  "op": "check_flag",
  "flag_id": 123
}
```

```json
{
  "op": "show_message",
  "bank": 5,
  "text_id": 12
}
```

```json
{
  "op": "trainer_battle",
  "trainer_id": 88
}
```

---

# 80. Flags 与 Variables

找到 SaveData：

```text
event flags
event vars
```

Runtime 输出：

```text
flag id/value
var id/value
```

剧情不能只看徽章。

---

# 81. Story Graph

节点：

```json
{
  "id": "virbank_gym",
  "requirements": [],
  "target": {},
  "completion": [],
  "next": []
}
```

---

# 82. Story Resolver

输入：

```text
flags
vars
map
current script
NPC state
inventory
badges
party
```

输出：

```text
current objective
completed nodes
blocked nodes
possible next nodes
```

并给 evidence。

---

# 83. Party

完整 PK5/party：

```text
species
form
PID
nature
gender
shiny
ability
level
EXP
HP
stats
IV
EV
moves
PP
status
held item
friendship
OT
egg
```

---

# 84. Inventory

完整：

```text
Items
Medicine
Poké Balls
TM/HM
Berries
Key Items
```

不硬编码。

---

# 85. Battle

必须找到稳定：

```text
BattleSystem
BattleContext
Battler
BattlePhase
```

不能靠扫描“像 Pokémon 的块”长期使用。

---

# 86. Battle State

```json
{
  "type": "trainer",
  "phase": "awaiting_command",
  "player": [],
  "enemy": [],
  "weather": null,
  "legal_actions": {
    "fight": true,
    "bag": true,
    "switch": true,
    "run": false
  }
}
```

---

# 87. Battle Action

AI 使用：

```text
battle.use_move(move_id)
battle.switch(slot)
battle.use_item(item_id, target)
battle.throw_ball(item_id)
battle.run()
```

Runtime 自己完成菜单。

---

# 88. Action Engine

所有操作都是事务。

```text
precondition
↓
input
↓
observe
↓
postcondition
↓
completed / retry / failed / interrupted
```

---

# 89. 不能固定 sleep 判成功

错误：

```text
press A
sleep 0.5
return success
```

正确：

```text
press A
↓
等待 game state 改变
↓
确认 dialogue/menu/battle 已进入
```

---

# 90. Input Engine

基础动作：

```text
press
hold
release
touch
wait_frames
```

高层：

```text
move_one_tile
turn
interact
open_menu
select
```

---

# 91. Navigate

正式 API：

```json
{
  "target": {
    "type": "npc",
    "id": 7
  }
}
```

Runtime 自动完成：

```text
route
movement
replan
```

---

# 92. Navigation Interrupt

如果：

```text
wild battle
dialogue
script event
map transition
NPC blocker
```

则：

```text
navigation interrupted
```

处理后重新规划。

---

# 93. Recovery

必须处理：

```text
unexpected battle
wrong menu
wrong warp
blackout
NPC moved
route blocked
unknown UI
bridge disconnect
BizHawk paused
ROM changed
savestate loaded
```

---

# 94. Savestate Event

Bridge 监听：

```text
event.onloadstate
```

savestate 后：

```text
state_version reset/bump
清理 pointer cache
重新验证 profile symbols
重新采样所有重要状态
```

---

# 95. State Version

任何重要变化 bump：

```text
map
player position
mode
dialogue
cursor
battle
party
inventory
story flag
runtime entities
```

Action 请求带：

```text
expected_state_version
```

避免过期操作。

---

# 96. Event Stream

WebSocket 和 AI 内部事件：

```text
bizhawk_connected
bizhawk_disconnected
rom_changed
profile_changed
map_changed
player_moved
npc_moved
dialogue_started
dialogue_choice_required
menu_opened
battle_started
battle_phase_changed
pokemon_caught
battle_ended
item_obtained
story_objective_changed
navigation_started
navigation_interrupted
navigation_completed
runtime_unknown
```

---

# 97. REST API

基础：

```text
GET  /api/health
GET  /api/bizhawk/status
GET  /api/bizhawk/doctor
GET  /api/bizhawk/domains

GET  /api/state
GET  /api/state/world
GET  /api/state/screens
GET  /api/state/party
GET  /api/state/inventory
GET  /api/state/battle
GET  /api/state/story

GET  /api/maps
GET  /api/maps/{id}
GET  /api/maps/{id}/nav
GET  /api/maps/{id}/entities
GET  /api/world/graph

POST /api/actions/navigate
POST /api/actions/interact
POST /api/actions/dialogue
POST /api/actions/menu
POST /api/actions/battle

POST /api/research/snapshot
POST /api/research/diff
POST /api/research/scan
POST /api/research/watch
```

---

# 98. WebSocket

```text
/ws/runtime
```

发送：

```text
state_delta
events
action_status
doctor changes
```

不要每次发送完整 4 MB state。

---

# 99. 正式 MCP

工具数量保持小。

```text
observe
query
navigate
interact
dialogue
menu
battle
use
wait
```

---

# 100. observe

默认紧凑：

```json
{
  "mode": "overworld",
  "location": "...",
  "objective": "...",
  "focus": null,
  "ready": true
}
```

---

# 101. query

domain：

```text
system
bizhawk
screen
world
map
nearby
npc
story
dialogue
party
pokemon
inventory
battle
```

---

# 102. Research MCP

和正式 MCP 分离。

工具：

```text
bizhawk_doctor
bizhawk_domains

memory_read
memory_read_batch
memory_snapshot
memory_diff
memory_scan
memory_watch

profile_get
profile_validate
profile_set_candidate

rom_map
rom_script
rom_text
```

游戏 AI 默认无权使用：

```text
raw write memory
```

---

# 103. Web App 主导航

建议：

```text
Overview
World
Runtime
Story
Battle
Party
BizHawk Inspector
Memory Research
Profile Lab
Logs
```

---

# 104. Overview

显示：

```text
BizHawk status
ROM
Profile
Current Mode
Current Map
Player
Current Objective
Current Action
Runtime Health
Latest Events
```

不把模拟器画面放主视觉中心。

---

# 105. Runtime 页面

面向开发：

```text
State Tree
Provenance
State Version
Raw Decoder Health
Sampling Rate
Last Changed
```

---

# 106. Story 页面

显示：

```text
Story Graph
current node
completed
blocked
evidence
current objective
target on map
```

---

# 107. Battle 页面

显示：

```text
battle phase
battlers
moves
HP
status
field
legal actions
decision history
```

---

# 108. Memory Research 页面

四区：

```text
Experiment
Snapshot
Candidate Table
Hex/Structure Inspector
```

必须能保存实验。

---

# 109. Profile Lab

显示：

```text
ROM hash
symbols
resolver
validator
confidence
evidence
last verified
```

操作：

```text
Run Validator
Compare Snapshot
Promote Candidate
Mark Unknown
```

---

# 110. Human Preview

可以有：

```text
BizHawk screen preview
```

但页面必须明确：

```text
HUMAN ONLY
NOT USED BY RUNTIME
```

不要给 Runtime 任何 screenshot dependency。

---

# 111. 启动流程

正常：

```text
1 backend
2 frontend
3 Runtime TCP server
4 BizHawk
5 Lua Bridge
6 handshake
7 doctor
8 profile select
9 RAM validation
10 semantic runtime ready
```

---

# 112. Attach 流程

BizHawk 已经运行：

```text
1 backend
2 打开 Lua Console
3 load black2_bridge.lua
4 Bridge HTTP hello
5 doctor
6 identify ROM
7 enumerate domains
8 validate RAM
9 session becomes ATTACHED
```

---

# 113. Bridge 断开

超过：

```text
2 秒无 heartbeat
```

可标：

```text
DEGRADED
```

超过：

```text
5 秒
```

标：

```text
DISCONNECTED
```

所有 action 立即取消。

具体阈值做配置。

---

# 114. ROM 切换

如果：

```text
rom_hash changed
```

必须：

```text
停止 action
清空 semantic state
重新 profile lookup
重新验证 memory
```

不能继续使用前一个 ROM 地址。

---

# 115. BizHawk Version 变化

保存：

```text
last_verified_bizhawk_version
```

如果当前版本和 Profile 测试版本不同：

```text
运行完整 Doctor
```

不要直接认为坏了。

Profile 主要绑定 ROM，不绑定 BizHawk。

但是 Bridge compatibility 需要检测。

---

# 116. 性能目标

正常：

```text
Bridge heartbeat <= 100 ms
high-frequency state <= 33 ms 或合理近似
普通 semantic query <= 50 ms
local path planning <= 50 ms
UI state push <= 100 ms
```

不要为追求 60 Hz 把完整 RAM 网络传输。

---

# 117. 日志

结构化日志：

```text
bridge
doctor
memory
profile
decoder
world
story
action
battle
api
```

每条有：

```text
timestamp
frame
session
state_version
```

---

# 118. Fixtures

所有重要 decoder 都从真实 RAM fixture 测试。

```text
tests/fixtures/cn/
tests/fixtures/us/
```

例如：

```text
overworld
party menu
dialogue
battle
bag
map transition
```

---

# 119. BizHawk Bridge Test

没有 Pokémon decoder 也必须先通过：

```text
hello
version
system
ROM
domains
read
hash
frame
pause
resume
input
heartbeat
```

---

# 120. Doctor Test

模拟：

```text
no process
process no bridge
wrong ROM
wrong system
bridge disconnected
RAM error
ready
```

UI 必须准确表示。

---

# 121. Reverse Engineering Test Discipline

找到地址必须至少：

```text
多个值
多个方向
多个地图
菜单进出
savestate reload
重新启动 ROM
```

验证。

---

# 122. 不接受单场景地址

例如：

```text
在立涌市 x 正确
```

不够。

必须：

```text
多个地图都正确
```

才能正式进入 Profile。

---

# 123. Story Test

关键 checkpoint：

```text
New Game
Gym 1
Gym 2
Gym 3
Gym 4
Gym 5
Gym 6
Gym 7
Gym 8
League
Champion
Hall of Fame
```

每个 checkpoint：

```text
flags
map
objective
expected next
```

---

# 124. World Test

代表类型：

```text
city
route
building
cave
gym
multi-floor
bridge
water
```

验证：

```text
model
coordinates
collision
warps
NPC
route
```

---

# 125. End-to-End 最终测试

条件：

```text
不给 AI screenshot
不给 AI 人工坐标
不给 AI 人工剧情提示
不给 AI 临时脚本
```

AI 只能调用正式 MCP。

从：

```text
New Game
```

运行到：

```text
Hall of Fame
```

---

# 126. 失败分类

E2E 失败必须分类：

```text
BRIDGE
PROFILE
MEMORY
DECODER
WORLD
COLLISION
NAVIGATION
UI
DIALOGUE
STORY
BATTLE
ACTION
RECOVERY
```

修一般能力。

不能写：

```text
if 当前地点 == 某地图:
    特殊走 17 步
```

---

# 127. 开发阶段顺序

必须按以下顺序。

---

## Phase 0：项目骨架

建立：

```text
backend
frontend
bridge
profiles
romdata
research
tests
```

并启动 Web UI。

---

## Phase 1：BizHawk Inspector

先完成：

```text
process probe
HTTP attach
socket managed mode
hello
version
system
ROM
frame
pause
domains
capabilities
doctor
```

此阶段结束时，即使还完全不懂 Pokémon RAM，也应该可以非常清楚地检查当前 BizHawk。

---

## Phase 2：Memory Lab

完成：

```text
read
read_batch
hash
dump
snapshot
diff
scan
correlation
watch
```

---

## Phase 3：ROM Identity + Profile

创建第一个中文 ROM profile。

只加入已经验证 symbol。

---

## Phase 4：基础 Runtime

找到：

```text
player
map
facing
movement
party
```

---

## Phase 5：ROM World Database

完成：

```text
Matrix
MapDefinition
BMD0
BTX0
Permission
Events
Warp
```

Web 可离线打开任意地图。

---

## Phase 6：3D World + Nav

完成：

```text
coordinates
collision
NavGrid
A*
World Graph
```

---

## Phase 7：Runtime Field Objects

找到：

```text
NPC runtime manager
positions
facing
movement
blocking
```

---

## Phase 8：UI Semantic Runtime

按顺序：

```text
game mode
dialogue
X menu
party menu
bag
summary
yes/no
choice
shop
PC
```

---

## Phase 9：Battle Runtime

稳定解析：

```text
BattleSystem
Battlers
phase
cursor
legal action
```

---

## Phase 10：Text + Script + Flags

ROM script IR。

Runtime script context。

event flags。

---

## Phase 11：Story Graph

建立完整主线。

---

## Phase 12：Action Runtime

从低层输入升级到：

```text
navigate
interact
dialogue
menu
battle
```

---

## Phase 13：正式 MCP

AI 开始只使用 Semantic API。

---

## Phase 14：英文 Black 2

新增 US Profile 和 Text Provider。

Runtime 不分叉。

---

## Phase 15：Full Run

New Game -> Hall of Fame。

---

# 128. AI Agent 开工总指令

代码 Agent 读到本文档后：

第一件事是创建全新目录，不读取任何旧项目架构。

然后只实现 Phase 0 和 Phase 1。

不要立刻开始猜 Black 2 RAM 地址。

先让 BizHawk Inspector 达到以下效果：

```text
能发现 EmuHawk.exe
能 Attach 已运行的 BizHawk
能 Managed Launch BizHawk
能看到 BizHawk version
能确认 NDS
能看到 ROM name/hash
能看到 frame/paused/turbo
能列出所有 Memory Domains
能显示每个 Domain size
能读取小块 RAM
能 hash RAM
能发送一次安全 input probe
能显示 capability
能运行 Doctor
```

只有 Doctor 达到：

```text
MEMORY READY
```

之后才创建 Pokémon Profile。

---

# 129. Bridge Probe 最小行为

`probe.lua` 应当尽量简单。

逻辑：

```lua
print version
print system
print ROM
print domains
print sizes
print frame
print current domain
print event scopes
print socket status
```

不要在 probe 阶段：

```text
扫描 Pokémon 地址
写内存
自动走路
加载 savestate
```

---

# 130. black2_bridge.lua 行为

Bridge 是长期运行脚本。

启动：

```text
collect capabilities
send hello
start heartbeat
receive commands
execute commands
return result
frameadvance/yield
```

绝不能因为网络断开永久卡死 EmuHawk。

网络读取必须：

```text
短 timeout
non-fatal
retry
```

---

# 131. Bridge 安全

允许：

```text
RAM read
input
pause/resume
savestate research
```

默认禁止：

```text
任意 RAM write
```

Research 模式如果开放 RAM write：

```text
显式 enable
明确日志
限定 domain/range
```

---

# 132. 当前 BizHawk 兼容性注意

当前官方 README 列出的 NDS core 是：

```text
melonDS
```

Lua Console 官方路径：

```text
Tools -> Lua Console
```

官方 CLI 已支持：

```text
--lua
--socket-ip
--socket-port
```

Lua API 已提供：

```text
client.getversion
emu.getsystemid
gameinfo.getromname
gameinfo.getromhash
memory.getmemorydomainlist
memory.getmemorydomainsize
memory.read_bytes_as_binary_string
memory.hash_region
event.availableScopes
event.on_bus_write
joypad.set
joypad.setanalog
comm.httpGet
comm.httpPost
comm.mmf*
comm.socketServer*
```

项目应针对运行时实际 capability 检测，而不是只靠文档假设。

---

# 133. BizHawk 检查失败时的处理

## Process 有，Bridge 无

显示：

```text
BizHawk is running, bridge not loaded
```

UI 提示：

```text
Tools -> Lua Console -> Open Script -> black2_bridge.lua
```

---

## Bridge 有，NDS 无

显示：

```text
Wrong system
```

停止 decoder。

---

## NDS 有，ROM hash 未知

显示：

```text
Research Mode
```

允许 RAM Lab。

---

## Memory Domains 为空

显示：

```text
Core memory interface unavailable
```

不要继续扫描。

---

## Domain read error

记录：

```text
domain
addr
length
exception
```

标记这个 domain 不可用。

---

## Touch 不工作

先检查：

```text
Touch X/Y host mouse binding conflict
```

不要回退屏幕识别。

---

# 134. 禁止事项

从项目第一天就禁止：

```text
Screenshot gameplay decoder
OCR dialogue
RGB menu detector
硬编码当前剧情
硬编码 money
硬编码 badge
硬编码 inventory
硬编码 NPC count
地图专用 walking script
固定 sleep 后认为操作成功
通过 Windows process memory 读取 RAM
把未知字段填默认值
把未经验证地址写进正式 Profile
```

---

# 135. 代码质量

所有模块：

```text
typed
documented
testable
no cyclic dependency
```

依赖方向：

```text
Bridge
↓
Memory/Profile/ROM
↓
Decoders
↓
State/World/Story
↓
Actions
↓
API/MCP/UI
```

上层不能反向污染底层。

---

# 136. 一个重要设计判断

LLM 不是实时控制器。

LLM 负责：

```text
目标
剧情选择
队伍规划
Boss 策略
资源决策
```

算法负责：

```text
A*
菜单状态机
输入
等待条件
路径执行
状态验证
```

所以完整通关不应该每走一格调用 AI。

---

# 137. 最终 AI 使用体验

AI：

```text
observe()
```

返回：

```json
{
  "mode": "overworld",
  "map": "Virbank City",
  "objective": "Challenge the Gym",
  "ready": true
}
```

AI：

```text
navigate(target=current_objective.target)
```

Runtime：

```text
自动规划并移动
```

遇野怪：

```text
battle_started
```

AI：

```text
battle(...)
```

战斗结束：

```text
navigation resume
```

到达 NPC：

```text
interact(...)
```

普通对话：

```text
dialogue.finish()
```

剧情 flag 改变：

```text
Story Resolver
```

返回新目标。

这才是最终系统。

---

# 138. 第一阶段 Definition of Done

Phase 1 完成条件：

```text
[ ] 前端 BizHawk Inspector 页面存在
[ ] 能发现运行的 EmuHawk
[ ] 能在已运行 BizHawk 里手动加载 Bridge 并 Attach
[ ] 能 Managed Launch
[ ] client.getversion 成功
[ ] emu.getsystemid == NDS
[ ] ROM name/hash 成功
[ ] framecount 成功
[ ] pause state 成功
[ ] memory domains 成功
[ ] 每个 domain size 成功
[ ] 小范围 binary read 成功
[ ] hash_region 成功
[ ] capability discovery 成功
[ ] event scopes 成功
[ ] joypad button probe 成功
[ ] Doctor 页面给出明确 READY/PARTIAL/ERROR
```

如果这些没有全部完成：

```text
禁止开始 Pokémon 逆向。
```

---

# 139. 第二阶段 Definition of Done

Memory Lab：

```text
[ ] full RAM snapshot
[ ] selected snapshot
[ ] snapshot diff
[ ] exact value scan
[ ] changed/unchanged filter
[ ] numeric correlation
[ ] candidate list
[ ] narrow write watch
[ ] experiment persistence
```

---

# 140. 最终 Definition of Done

完全关闭 AI 的截图输入。

AI 只通过：

```text
Semantic MCP
```

从：

```text
New Game
```

完成：

```text
Hall of Fame
```

途中：

```text
地图
NPC
剧情
对话
菜单
队伍
背包
战斗
导航
```

都来自本项目内部结构化状态。

---

# 141. 参考依据

实现 BizHawk Bridge 时优先核对：

```text
BizHawk official README
https://github.com/TASEmulators/BizHawk

BizHawk Lua Functions
https://tasvideos.org/Bizhawk/LuaFunctions

BizHawk ArgParser source
src/BizHawk.Client.Common/ArgParser.cs
```

需要参考 Gen V ROM 结构时可以研究：

```text
swan
SwissArmyKnife
CTRMap-CE
PokeLua
```

但第三方项目只能作为：

```text
格式参考
结构参考
地址候选
验证证据
```

任何 RAM 地址进入当前 ROM Profile 前都必须由本项目实验验证。

---

# 142. 交给代码 Agent 的一句最终要求

从零建立这个项目。

首先解决“可靠地检查、附加和控制我当前正在运行的 BizHawk”。

BizHawk 内部状态只能通过 BizHawk 官方 Lua / Memory Domain 接口读取。

在 BizHawk Doctor、Memory Lab 和 ROM Profile 基础稳定之前，不做自动通关脚本。

最终建设的是一个 Pokémon Black 2 Semantic Runtime，而不是一个按键机器人。
