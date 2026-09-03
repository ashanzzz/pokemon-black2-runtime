# EXP_012 — 手动对话检查点采集

## 目的

让操作者在确认 NDS 屏幕状态后，点击网页按钮保存同一 BizHawk bridge 帧的原始 RAM。网页标签是人工观察记录，不是解析器对可视行、TextPrinter 状态或说话 NPC 的结论。

## 使用

1. 重启项目的 `run_runtime.py`，让新增的 `/api/dev/dialogue_checkpoint` 路由加载。
2. 在浏览器打开 `http://127.0.0.1:8765/dialogue-checkpoints`（或 `/frontend/dialogue-checkpoints.html`）。
3. 确认页面显示 `BizHawk bridge 已连接`。游戏内的 A 键仍由你手动按，网页不会注入按键。
4. 依次在画面达到对应状态时点击：
   - 对话前（站在 NPC 面前，尚未按 A）；
   - 第 1 页：`科学的力量真是惊人！`；
   - 第 2 页：`现在可以用通信` / `和100个人`；
   - 滚屏过渡阶段：`和100个人` / `同时游戏！`（两行短暂同时可见；本次实机没有独立稳定第三页）；
   - 对话结束：在游戏里再按一次 A，窗口消失、对话锁解除后。
5. 如需重复实验，不覆盖旧文件；服务端会以 UTC 时间和 bridge frame 生成新文件。

连接诊断会写入 `logs/bridge_transport.log`，网页中的“查看 Bridge 连接日志”按钮对应 `/api/dev/bridge_log`。

## 证据内容

每个 JSON 包含 `frame`、人工 `label`/`operator_note`，以及同一次 `memory.read_batch` 的五个观察窗口：script/message state、MsgBuffer、printer candidate、pointer candidates、player actor candidate。`visible_lines`、`text_printer_state`、`speaker_actor` 在证据不足时明确保持 `unresolved`。

## 下一步

完成这五个检查点后，将这些 JSON 作为一组对比 `frame`、控制码、指针和 Actor 候选的变化；只有出现跨检查点且结构一致的证据，才提升当前可视行或说话 NPC 字段的置信度。若滚屏过渡阶段来不及点击，至少保留第 2 页和对话结束，并在备注中说明。

本实验已完成两轮五点采集；两轮的 message/script/printer/pointer 原始区逐字节一致，确认标签顺序和滚屏过渡判断可复现。
