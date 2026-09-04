# Workbench v9 UI / UX 架构

## 设计目标

本项目不是普通游戏辅助器，也不是 AI Dashboard。它是一个长期运行的逆向工程 Workbench。

信息组织以“对象 + 证据 + 状态”为核心，而不是“页面 + 按钮”。

## 五区结构

1. **Activity Rail**：World / Player / Dialogue / Memory / Evidence / Monitor / Tools
2. **Explorer**：当前 workspace 的对象树
3. **Main Editor**：3D World 或文档型编辑器
4. **Context Inspector**：选中什么，就解释什么
5. **Bottom Dock**：Events / Asset Errors / Navigation / Calibration / Performance / Raw

底部再保留一个低高度 Status Bar，显示 HTTP、Bridge、Player、Scene、Zone、Frame、FPS 与 RAM read policy。

## World workspace

World 是唯一主地图产品界面，只呈现 3D 世界。

Explorer：

```text
Current Scene
Player
Terrain
Buildings
Runtime Actors
Warps
Triggers
```

3D 对象支持 raycast selection。选中对象后 Inspector 使用统一 contract：

```text
kind
id
label
confidence
facts
raw
actions
```

### Building

必须能单独看到：

- instance id
- model UID
- DoorUID
- world position
- rotation
- ROM source
- raw object

### NPC

必须区分：

- Runtime actor 是否存在
- model_id
- WPos / GPos
- facing
- original resource mapping 是否仅 candidate

### Player

必须区分：

- position truth
- orientation truth
- movement state
- renderer resource state

“位置正确”和“原版贴图成功”是两个不同维度。

## i18n

- 默认 `zh-CN`
- 支持 `en`
- storage key: `black2.workbench.locale`
- 不根据浏览器语言自动把默认切到英文

## Legacy tools

Controller、RAM Dumper、Memory Tracer、Dialogue Checkpoints 仍保留，但从一级产品导航降级到 Tools。

原因：这些工具有逆向价值，但不应继续形成新的信息架构。

## 性能 UX

UI 中必须明确区分：

- cached read
- bounded read
- heavy read
- write-input action

Workbench 默认只执行 cache / low-cost read。重型取证必须用户显式触发。
