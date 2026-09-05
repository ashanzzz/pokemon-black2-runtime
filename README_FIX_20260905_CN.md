# Pokémon Black 2 Runtime 修复包说明

基线：`ashanzzz/pokemon-black2-runtime@7bd1de29a8ead9fdbd5c8e565ae7ad9728f7cddc`。

## 本次修复

### 1. 对话运行时绑定

新增缓存式 `ScriptWork -> talkmsgwin -> TCBL -> Bitmap/PixelData` 定位。

- 正常轮询只读取缓存地址对应的小范围 RAM。
- 只有当前消息活动、且缓存失效时，才在 `0x02320000..0x02338000` 对 `talkmsgwin.c` 做一次受限搜索。
- 定位读不能直接作为“当前可见文字”；成功后会重新原子采样 StrBuf、TCBL、Bitmap 和 PixelData，再交给 VisibleTextLedger。
- Loaded Stream 和当前屏幕 Visible Text 分开。
- 硬件 message flag 存在但 renderer 未绑定时保持 UNRESOLVED，不再让空对话 timeline 持续几分钟。
- ScriptWork `+0x08` ParentActor 若与 FieldActor 结构自洽，只作为 PROBABLE speaker 绑定，不通过文本猜名字。

用户提供的 `进门前` 物理 RAM 已验证：

- ScriptWork `0x0224758C`
- talkmsgwin `0x02321DDC`
- TCBL `0x02324240`
- PixelData `0x023232F4`
- Phase `WAIT_PAGE`
- 当前可见两行：`哎？你们两个很要好地` / `在找白露啊！`
- 下一页 `还没找到她吗？` 不会提前泄漏
- ParentActor `0x0223DBE4`，Model 251，GPos `(4,0,5)`

### 2. Player 坐标语义

Semantic State 不再把 GPos 填进 `player_world_pos`。

- `player_grid_pos` 读取 FieldActor GPos。
- `player_world_pos` 读取 FieldActor WPos。
- 本样本为 GPos `(5,0,5)`，WPos `(88,0,88)`。

### 3. NPC 场景成员

保留 FieldActor 原始 `zone_id`，新增独立的 `scene_membership`。

- raw ZoneID 与 PlayerState ZoneID 一致时为 PROBABLE 当前场景。
- raw ZoneID 为 0，但同一 coherent ActorSystem 且 GPos 位于当前 Mapper 范围时，只标 CANDIDATE 当前场景。
- 不把 raw ZoneID 0 改写成 428。

当前证据：

- Slot 0 / Model 251：GPos `(4,0,5)`，WPos `(72,0,88)`，raw Zone 428。
- Slot 2 / Model 291：GPos `(5,0,6)`，WPos `(88,0,104)`，raw Zone 0，因此当前场景成员仍只标 CANDIDATE。

### 4. World Lab 显示

新增一个轻量前端修正层，不改变 canonical world 坐标。

- Player 和 NPC 的 FieldActor.WPos 锚点增加始终可见的位置环，避免原版 sprite/model 资源失败或被场景遮挡时“人不见了”。
- 不再把所有 glTF 材质永久强制为 DoubleSide。原始 glTF 材质的 sidedness 会在 atomic scene swap 后恢复。室内墙体/家具背面不会因为调试代码被额外显示和参与视觉遮挡。
- NPC renderer 使用新的 `same_current_scene`，因此打开 NPC 层后可看到 `(4,5)` 与 `(5,6)` 两个 live actor。

材质 sidedness 修复属于 CANDIDATE UI 修复。它符合 glTF 原始材质语义，但本环境没有用户电脑上的 BizHawk + 浏览器实时画面，不能标 VERIFIED。

## 应用到现有仓库

在 PowerShell 中：

```powershell
.\APPLY_TO_EXISTING_REPO.ps1 -RepoPath C:\path\to\pokemon-black2-runtime
```

脚本会检查目标 Git HEAD 是否是本修复的 GitHub 基线，然后复制本包内的修改文件。若 HEAD 不一致会停止，不会强行覆盖。

## 生成完整 Git 工作副本

若需要从零生成一个完整 Git 工作副本：

```powershell
.\MATERIALIZE_FULL_REPO.ps1 -Destination C:\path\to\pokemon-black2-runtime-fixed
```

该脚本使用 Git 克隆公开仓库、checkout 到本修复基线，再覆盖本包修改。需要网络和 Git。

若要直接生成清理后的完整源码 ZIP：

```powershell
.\MATERIALIZE_FULL_ZIP.ps1 -OutputZip C:\path\pokemon-black2-runtime-v10.1-fixed-full.zip
```

该脚本会克隆精确基线、应用修复、移除 `.git`、runtime 和生成缓存，再输出完整源码 ZIP 与 SHA256。

## 用户电脑上的建议实测

1. 在“进门前”同一对话第一页停住。Dialogue 页应只显示当前两行，Loaded Stream 可包含后续页。
2. 按 A 翻页，确认第一页清除后再显示后续行。
3. World Lab 应把 Player 固定在 GPos `(5,5)` 对应 WPos `(88,88)`。
4. 打开“显示 NPC”。小女孩应位于 `(4,5)`，另一个 live actor 应位于 `(5,6)`。
5. 反复进出建筑，确认 atomic scene swap 无堆叠，墙体/家具没有因双面材质异常增多。
6. 检查 Inspector。Slot 2 的 raw ZoneID 必须仍显示 0，scene membership 只能是 CANDIDATE。


## Runtime 生命周期修复（v10.1）

- `BLACK2_LAUNCHER.cmd`：按 checkout 加单实例互斥；重复点击不会再创建第二套 launcher/backend。
- `STOP_BLACK2.cmd`：只停止这个 checkout 拥有的全部 `run_runtime.py` 后端进程（包括 Web `/restart` 产生的 replacement）；**不关闭 EmuHawk**。
- `CLOSE_EMUHAWK.cmd`：只对本 checkout 启动并记录的 EmuHawk 发送 `WM_CLOSE`；**不停止后端**。
- GUI / tray 中“停止后端服务”和“关闭模拟器”是两个独立动作。
- 不按端口或进程名全局误杀 Python/EmuHawk。

### 一键生成物理完整源码 ZIP

双击 `BUILD_FULL_FIXED_ZIP.cmd`。脚本会 clone `https://github.com/ashanzzz/pokemon-black2-runtime.git`，固定 checkout 到 `7bd1de29a8ead9fdbd5c8e565ae7ad9728f7cddc`，应用本包修复，清理 `.git`、runtime、日志、cache、pyc 后生成完整源码 ZIP 并输出 SHA256。
