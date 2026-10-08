# Pokémon Gen 5 (Black 2 / White 2) 开源逆向工程与生态权威索引

本项目遵循“立足开源巨人肩膀，拒绝盲猜内存”的工程原则。以下精选收录了全球宝可梦逆向与自动化社区最成熟的核心开源项目，按优先级分为三大梯队：

---

## 顶级核心参考（5 星推荐 ⭐⭐⭐⭐⭐）

### 1. BizHawk
- **仓库**：[TASEmulators/BizHawk](https://github.com/TASEmulators/BizHawk)
- **定位**：模拟器核心底座
- **核心价值**：
  - 提供确定性 MelonDS 核心、Memory Domain 划分、Frame Advance 逐帧推进、Savestate 快速读档；
  - 运行时控制的权威基准。

### 2. BizHawk External Tools Wiki
- **仓库/Wiki**：[TASEmulators/BizHawk-ExternalTools/wiki](https://github.com/TASEmulators/BizHawk-ExternalTools/wiki)
- **定位**：BizHawk 扩展开发指南
- **核心价值**：
  - C# Tool 编写规范、EmuHawk 内部 API 接口与线程调度模型；
  - 了解模拟器 UI 线程与后台 IPC 通信的底层细节。

### 3. bizhawk-mcp-native
- **仓库**：[stealthc/bizhawk-mcp-native](https://github.com/stealthc/bizhawk-mcp-native)
- **定位**：AI 代理控制模拟器参考架构
- **核心价值**：
  - 标准 `LLM → MCP Tool → Emulator` 架构实践；
  - 批量内存调用（Batching）、版本 Pin 锁定、自动化测试与真机端到端验证范例。

### 4. SwissArmyKnife
- **仓库**：[PlatinumMaster/SwissArmyKnife](https://github.com/PlatinumMaster/SwissArmyKnife)
- **定位**：Gen 5 ROM 静态解析瑞士军刀
- **核心价值**：
  - 完整解析 B2/W2 的地图容器（Map Containers）、文本容器（Text Containers）、事件脚本（Event Scripts）、Zone 头文件、NPC 列表、Trigger 触发器、Warp 跳跃点、Interactables 与野外遇敌（Encounters）；
  - 本项目静态 ROM 解析器的首选差分比对预言机（Differential Oracle）。

### 5. swan
- **仓库**：[ds-pokemon-hacking/swan](https://github.com/ds-pokemon-hacking/swan)
- **定位**：Black 2 / White 2 反编译与逆向数据库
- **核心价值**：
  - 官方级 C 结构体定义（`FieldSystem`、`PlayerActor`、`TextPrinter`、`TaskManager` 等）；
  - 维护 `root.swandb`、`IRDO.yml`、`IREO.yml` 等游戏 RAM 符号与函数地址库，本项目动态内存解析的核心理论源泉。

---

## 深度领域参考（4 星推荐 ⭐⭐⭐⭐）

### 6. CTRMap-CE
- **仓库**：[PlatinumMaster/CTRMap-CE](https://github.com/PlatinumMaster/CTRMap-CE)
- **定位**：Gen 5 3D 地图与关卡编辑器
- **核心价值**：
  - 权威解析 Gen 5 地图结构、事件模型与脚本字节码；
  - 本项目 3D 地形与建筑网格提取（BMD0/NSBMD）的底层格式参考。

### 7. PKHeX
- **仓库**：[kwsch/PKHeX](https://github.com/kwsch/PKHeX)
- **定位**：Pokémon 数据模型与算法参考
- **核心价值**：
  - 纯 C# 实现的宝可梦数据模型、个体值/努力值、技能位、道具位结构体；
  - 第五世代队伍 Shuffle45 混淆表（32 组 32 字节 BlockPosition 置换）与 Checksum 校验算法；本项目 `party_runtime.py` 已完整吸收其算法。

### 8. PokéBot
- **仓库**：[Kakumi/Pokebot](https://github.com/Kakumi/Pokebot)
- **定位**：宝可梦自动化与机器人参考
- **核心价值**：
  - 自动化脚本、内存实时轮询、确定性输入注入流程参考。

### 9. NTRGhidra
- **仓库**：[pedro-javierf/NTRGhidra](https://github.com/pedro-javierf/NTRGhidra)
- **定位**：NDS 静态逆向 Ghidra 插件
- **核心价值**：
  - 专为 Nintendo DS 定制的 Ghidra Loader，完美支持 Overlay 动态加载与符号对齐。

### 10. ndspy
- **仓库**：[RoadrunnerWMC/ndspy](https://github.com/RoadrunnerWMC/ndspy)
- **定位**：Python NDS 文件格式解析库
- **核心价值**：
  - 极高精度的 ROM、NARC 容器解包库，用于 Python 端静态数据提取。

---

## 高级进阶参考（3 星推荐 ⭐⭐⭐）

### 11. PMC (PokéModdingCore)
- **仓库**：[ds-pokemon-hacking/PMC](https://github.com/ds-pokemon-hacking/PMC)
- **定位**：B2/W2 代码注入框架
- **核心价值**：
  - 通过 hook overlay 加载并在游戏内直接运行 C 代码；
  - 适用于后期通过内部 Hook 主动向外暴露游戏状态（如直接输出 `FieldSystem` 状态）。

### 12. White2Upgrade
- **仓库**：[ds-pokemon-hacking/White2Upgrade](https://github.com/ds-pokemon-hacking/White2Upgrade)
- **定位**：B2/W2 大型 ROM Hack 与代码注入实战案例
- **核心价值**：
  - 真实展示如何组合使用 PMC 与 swan 进行函数调用与功能扩展。

---

## 本项目吸收与落地策略

```text
1. 静态数据核对：拿 SwissArmyKnife / CTRMap-CE 做差分比对，确保 ROM 地图与 NPC 100% 正确；
2. 动态内存对齐：拿 swan / PKHeX 结构体作为权威蓝本，在 IREJ01 (汉化版) 上微调物理基址；
3. 控制与协议：参考 bizhawk-mcp-native 批处理思维，以最轻量化的 Lua Socket 暴露确定性 API；
4. 自主开发：在 Python 端完全原生实现所有解密与调度算法，拥有独立、无版权负担的代码库与 470+ 单元测试。
```
