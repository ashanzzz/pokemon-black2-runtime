# ROM 原生 3D 地图

v2 现在提供一条独立的 ROM 原生地图预览链路。它从 BizHawk 当前加载的 ARM9 内存中确认 BMD0/BTX0，再回到本地 Black 2 ROM 读取对应地图块和官方材质，最后使用 Apicula 转成 GLB 供浏览器显示。

## 打开

启动 v2 服务后访问：

```text
http://127.0.0.1:8765/map/native
```

总览页的“3D 世界”区域也保留了入口。原来的 `/api/world/map/{map_id}` 合成几何接口没有删除，其他功能可以继续使用。

## 依赖

- BizHawk 中加载 Black 2，并运行 `bridge/bizhawk/black2_bridge.lua`。
- 重新加载桥接脚本，使 `memory.scan_headers` 生效。
- Black 2 ROM 放在默认位置，或设置 `BLACK2_ROM_PATH`。
- Apicula 位于 `runtime/tools/apicula/apicula.exe`；v2 已随本次移植放入该位置。

地图缓存写入 `runtime/native_map_cache/live`。缓存只保存当前地图窗口的提取源文件和 GLB，不修改 ROM。

## 接入边界

本次只移植地图模型、材质、矩阵、实时玩家位置和浏览器渲染。v2 原有的 BizHawk 输入、状态、语义接口保持不变；地图事件对象暂不凭空生成，因此原生页面不会显示尚未验证来源的 NPC、家具或传送点。

如果页面提示“当前没有加载地图资源”，先在游戏中进入可见的户外或室内地图，再点“重新加载”。切换地图时页面会自动重试并更新。
