# TextPrinter 状态转移与运行时状态机参考矩阵 (STEP 1-2)

> **最高原则**：参考项目（Gen 4 pokeplatinum / pokeheartgold）只提供机制模型和状态机流转指纹，绝对不能把其中的具体数字（如 `state == 2` 或 offset）直接假定为 Black 2 的事实。必须通过实机 RAM 受控实验和反汇编闭环验证。

---

## 1. 参考反编译项目 (Gen 4) 核心架构剖析

### 1.1 TextPrinter 核心结构构成
在 pokeplatinum / pokeheartgold 中，一个运行时的 `TextPrinter` 结构体本质上是一个**基于协程/状态机的流式光栅化任务（Task）**：

```c
struct TextPrinter {
    struct TextPrinterTemplate template;  // 包含目标 Window 指针、字体 ID、前景色、背景色、阴影色
    void (*callback)(struct TextPrinterTemplate *, u16);
    u8 subProcess;       // 核心状态机 state: 决定当前处于出字、等待、滚屏还是清屏
    u8 textSpeed;        // 打字速度 (延迟帧数)
    u8 textSpeedCounter; // 速度计数器 (每递减到 0 消费下一字符)
    u8 letterSpacing;    // 字间距
    u8 lineSpacing;      // 行间距
    u8 scrollDistance;   // 滚屏剩余像素 (用于逐帧平滑平移)
    const u16 *currentChar; // 当前正在消费的字符源指针 (UCS-2 / Gen 4 编码流)
    u16 currentX;        // 目标 Window 局部光标 X
    u16 currentY;        // 目标 Window 局部光标 Y
    u16 delayCounter;    // 等待计数
};
```

### 1.2 Gen 4 的状态转移图景（State Transition Fingerprints）

| 状态语义 (Gen 4 Enum) | 进入触发条件 | 核心行为与对外部对象的影响 | 退出条件 |
| :--- | :--- | :--- | :--- |
| **`0: HANDLE_CHAR`**<br>(出字与排版) | 启动打印，或从各等待/滚屏状态返回 | 从 `currentChar` 读取 1 个 token：<br>1. 普通字符：查字体字模，调用 `BlitBitmapRect4Bit` 将字模光栅化写入 Window 的 PixelData，`currentX += glyphWidth + letterSpacing`。<br>2. 换行符：`currentX = 0`, `currentY += fontHeight + lineSpacing`。<br>3. 控制码：根据控制命令设置下个状态并前移 `currentChar`。 | 遇到延迟码转 `WAIT`；遇到等待翻页转 `CLEAR`；遇到等待滚屏转 `START_SCROLL`；字符串结束释放结构。 |
| **`1: WAIT`**<br>(字符延迟/暂停) | 读到延迟控制码 (PAUSE) | 逐帧递减 `delayCounter`。保持当前像素不变。 | `delayCounter == 0` -> 返回 `HANDLE_CHAR`。 |
| **`2: CLEAR`**<br>(翻页等待与清屏) | 读到分页等待控制码 | **阶段 A (Wait)**：检查玩家按键输入（如按 A/B）。如果玩家未按键，停留在本状态，保持屏幕双行稳定显示。<br>**阶段 B (Action)**：捕获到 A 键按下瞬态，立即调用 `FillWindowPixelBuffer` 清空整个 Window 像素区（重置为底色），并将 `currentX = 0`, `currentY = 0`。 | A 键输入触发清屏后 -> 返回 `HANDLE_CHAR`，读取新一屏文字。 |
| **`3: START_SCROLL`**<br>(滚屏等待与初始化) | 读到滚屏等待控制码 | **阶段 A (Wait)**：检查玩家按键输入。未按键时保持屏幕双行稳定显示。<br>**阶段 B (Action)**：捕获到 A 键按下瞬态，计算 `scrollDistance = fontHeight + lineSpacing`（通常为 16 像素），转移到 `SCROLL` 状态。 | A 键输入触发后 -> 转移到 `4: SCROLL`。 |
| **`4: SCROLL`**<br>(逐帧平滑滚屏) | 从 `START_SCROLL` 转移而来 | 每帧向上搬移 Window 像素缓冲区若干行（如每次 4 像素），新移出的首行像素被丢弃，空出的底部行填入底色；`scrollDistance -= step`；向 VRAM 标记脏区刷新。 | `scrollDistance == 0` -> 滚屏结束，`currentY` 维持在第二行基准线，`currentX = 0` -> 返回 `HANDLE_CHAR`。 |

---

## 2. Gen 5 (Pokémon Black 2 IREJ) 的格式演进与特征

### 2.1 控制码映射与连续流
经逆向审查证实，Gen 5 文本流采用了完全不同的控制码架构：
* 文本格式：`UCS-2` 宽字符，小端序。
* 终止符：`0xFFFF`。
* 简单换行：`0xFFFE` (相当于 `\n`)。
* 扩展控制码前缀：`0xF000`，紧随 `command_id (u16)` 与 `argc (u16)` 以及对应数量的参数。
  * `0xF000 0xBE01 0x0000 0xFFFE`：**CLEAR (分页等待并清屏)**。
  * `0xF000 0xBE00 0x0000 0xFFFE`：**SCROLL (翻页等待并滚屏)**。

### 2.2 关键时序误区纠偏：为什么 currentChar 不能等于 Visible Text？
在 Gen 4 和 Gen 5 的实现中：
1. 当状态机遇到 `CLEAR` 或 `SCROLL` 控制码时，**解释器会立即把控制码读取完毕，源指针 `currentChar` 会直接前移跳过该控制码**，停在下一屏的第一个待打印字符的地址上！
2. 随后状态机进入 `WaitButton`。**此时画面上显示的依然是上一屏/上一行的文字，但 `currentChar` 已经在物理上指向了下一屏！**
3. 如果此时直接截取 `source[0 : currentChar]`，就会把尚未打印出来的下一屏文字提前读出（即“剧透” bug）。

---

## 3. Black 2 匿名事件与状态机模型 (Anonymous Event & State Model)

为了在 Black 2 RAM 逆向中不预设任何先验常数，定义一套完全由**运行时可观测行为**驱动的匿名事件与状态集合：

### 3.1 观测事件定义 (Observable Events)
* **`E0: GLYPH_RENDERED`**：`currentX` 前移，PixelData 对应字符字模区域发生非零像素写入。
* **`E1: NEWLINE`**：`currentX` 归零，`currentY` 增加行高（约 16px），无按键参与，PixelData 紧随其后在第二行发生像素增加。
* **`E2: WAIT_CLEAR_ENTER`**：第一屏或当前屏文字渲染完毕，`currentX/currentY` 停止步进，无像素变动，等待按键。
* **`E3: WAIT_SCROLL_ENTER`**：第二行满，遇到滚屏标记，`currentX/currentY` 停止步进，无像素变动，等待按键。
* **`E4: CLEAR_EXECUTE`**：捕获到按键，PixelData 整体被重置为背景色，`currentX=0`, `currentY=0`。
* **`E5: SCROLL_STEP`**：捕获到按键，`scrollDistance > 0` 且随帧递减，PixelData 所有光栅行逐帧向上平移。
* **`E6: SCROLL_COMPLETE`**：`scrollDistance` 归零，平移停止，`currentX` 位于第二行行首。
* **`E7: MESSAGE_CLOSE`**：遇到 `0xFFFF`，对话锁释放，窗口图层关闭。

### 3.2 匿名状态转移矩阵 (Black 2 Anonymous States S0-S5)

| 匿名状态 | 行为特征指纹 (Fingerprint) | 内存可观测特征 | 候选语义 |
| :---: | :--- | :--- | :--- |
| **`S0`** | 出字阶段。逐帧或按速度计数递增，X 递增，PixelData 持续局部变化。 | 计数器变化，`currentChar` 连续递增，PixelData hash 每若干帧变化一次。 | `S_PRINTING` |
| **`S1`** | 等待按键（翻页/清屏）。长时间不按键则保持恒定，PixelData 稳定。按 A 键后立即触发整屏清空。 | 某一状态字节稳定维持为常数 $C_{clear}$，按 A-edge 后瞬间改变。 | `S_WAIT_CLEAR` |
| **`S2`** | 等待按键（滚屏）。长时间不按键则保持恒定，PixelData 稳定。按 A 键后触发逐帧滚屏。 | 某一状态字节稳定维持为常数 $C_{scroll}$，按 A-edge 后瞬间进入滚屏。 | `S_WAIT_SCROLL` |
| **`S3`** | 逐帧平移。持续若干帧，每帧 PixelData 行向上移动，某距离变量递减至 0。 | `scroll_distance > 0` 逐帧单调递减至 0。 | `S_SCROLLING` |
| **`S4`** | 窗口已清空，准备从第一行绘制新字符。 | `currentX=0, currentY=0`，PixelData 为纯背景色。 | `S_CLEAR_POST` |
| **`S5`** | 对话结束/空闲。 | 硬件对话标志为 0，窗口图层为 0，打印器被销毁或标记为非活动。 | `S_INACTIVE` |

---

## 4. 下一步研究行动指南 (To STEP 3 & 4)
1. 在 `tools/runtime_memory_discovery.py` 中构建按帧统一记录的 Event Trace 采集器，重点采集 `0x0231FCB0`（打印器候选）、`0x02332C20`（控制窗候选）、以及 `0x023353C0`（3840 字节 PixelData）。
2. 用真实 A-Edge 捕捉：
   - 纯换行时（`E1`）的状态字段与坐标变化。
   - 等待清屏（`S1 -> E4 -> S4`）的临界字节跳转。
   - 等待滚屏（`S2 -> E5 -> S3 -> E6`）的临界字节与 `scrollDistance` 协同。
