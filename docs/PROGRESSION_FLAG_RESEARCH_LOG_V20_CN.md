# 剧情 Flag / 徽章 / 剧情门 / 故事编排逆向研究记录 V20（已更新）

日期：2026-10-04

## 一、重大突破：TrainerCard / Block 52 动态解析与徽章真值完成验证

通过反汇编 ARM Thumb 代码 `isBadgeObtained`、`getBadgeCount`、`SaveControl_GetBlockPtr`、`SaveData_GetBlock`，并深度结合开源项目 `references/PKHeX`（`SaveBlockAccessor5B2W2.cs` 与 `Misc5.cs`），已完全逆向并实机验证了 B2W2 保存数据块管理器：

### 1. 物理指针链
```text
GameData (0x0223B570)
  └─ +0x000: SaveControl (0x022051EC)
       └─ +0x010: SaveData 结构体 (0x02205284)
            ├─ +0x02C: 块描述符表头 (0x02205444) -> +0x14: 块描述符数组 (0x0220548C，共 73 项，每项 12 字节)
            └─ +0x034: 保存数据基址缓冲 (0x02205824)
```

### 2. Block 52 (Misc / TrainerCard)
- 索引：`Block 52`（`0x34`）
- 大小：`0xF0`（240 字节）
- 相对偏移：`0x21100`
- **实机绝对地址**：`0x02205824 + 0x21100 = 0x02226924`
- **内部数据布局**：
  - `+0x00` (u32)：当前玩家持有资金 Money = **$294,722**（已验证）
  - `+0x04` (u8)：8 位徽章掩码 Badge Bitmask = **0x3F**（二进制 `00111111`，已验证）
  - `+0x18` (u16)：PokeTransfer 迷你游戏分数
  - `+0x5C` (u16[48])：8 个道馆击破时的队伍宝可梦物种记录

### 3. 实机徽章解析结果（8 徽章状态）
- 徽章 1【基础徽章】（黑连 / 桧扇市）：**已获得 (True)**
- 徽章 2【毒性徽章】（霍米加 / 立涌市）：**已获得 (True)**
- 徽章 3【甲虫徽章】（亚堤 / 飞云市）：**已获得 (True)**
- 徽章 4【伏特徽章】（小菊儿 / 雷文市）：**已获得 (True)**
- 徽章 5【震动徽章】（菊老大 / 帆巴市）：**已获得 (True)**
- 徽章 6【飞翼徽章】（风露 / 吹寄市）：**已获得 (True)**
- 徽章 7【冰冻徽章】（夏卡 / 双龙市）：**未获得 (False)** ← 下一个主线道馆目标！
- 徽章 8【海浪徽章】（西子伊 / 青海波市）：**未获得 (False)**

---

## 二、合众全境剧情门（StoryGate）权威评估器

全合众地区 12 大主线道闸实时评估（`backend/black2/progression/story_gate.py`）：
* **已解锁通行（8 处）**：
  1. `gate_route19_aspertia`: 19号道路桧扇市大门 (Zone 427 -> 441)
  2. `gate_route20_virbank`: 20号道路立涌市大门 (Zone 446 -> 447)
  3. `gate_virbank_ferry`: 立涌市码头轮渡前往飞云市 (Zone 448 -> 378)
  4. `gate_castelia_route4`: 飞云市4号道路关卡 (Zone 378 -> 461)
  5. `gate_driftveil_drawbridge`: 帆巴吊桥 (Zone 464 -> 482)
  6. `gate_chargestone_cave`: 电石山洞入口 (Zone 485 -> 511)
  7. `gate_mistralton_plane`: 吹寄机场飞机前往反转山脉/山路镇 (Zone 107 -> 518)
  8. `gate_reversal_mountain_opelucid`: 反转山脉11号道路通往双龙市 (Zone 525 -> 120)
* **当前阻挡锁定（4 处）**：
  1. `gate_marine_tube_humilau`: 海底隧道通往青海波市 (Zone 120 -> 472，缺第7徽章)
  2. `gate_seaside_cave_humilau`: 21号道路海边洞穴通往青海波市 (Zone 465 -> 472，缺第7徽章)
  3. `gate_victory_road_badges`: 23号公路与冠军之路8枚徽章检验门 (Zone 565 -> 566，缺第7/8徽章)
  4. `gate_pokemon_league_champion`: 宝可梦联盟冠军大殿 (Zone 566 -> 570)

---

## 三、世界宏观路由与主线自主决策器（StoryRunner）

1. **宏观世界图谱 `WorldGraph`**：
   - 整合全合众 615 个 Zone、1089 个 Warp、100+ 缝隙 Matrix 边界及轮渡/航班；
   - 实现了双通道 A* 搜索：通道 1（Strictly Traversable）保障无阻碍畅行，通道 2（Diagnostic）在遇阻时定位具体卡点。
   - 接口：`GET /api/v1/navigation/global/route?to_zone={target}`。
2. **主线自主推进编排器 `StoryRunner`**：
   - 自动绑定当前主角 Zone、背包 6 徽章掩码、M8 夏卡双龙道馆目标；
   - 自动计算到达双龙道馆的 15 步宏观路径，生成结构化行动指南；
   - 明确指出第 1 步行动是搭乘立涌市轮渡前往飞云市（`Virbank-Castelia Ferry`）。
   - 接口：`GET /api/v1/agent/story/plan`。
3. **宝可梦中心恢复服务闭环**：
   - 扩展全合众宝可梦中心护士统一 counter 交互几何（`z+1` 柜台，`z+2` 站位）；
   - 对接 `PlayerPartyDecoder` 在恢复前后执行全员 HP/PP/异常状态差分校验；
   - 消除 `party_hp_completion` unresolved 限制，恢复能力标记为 `verified`。
