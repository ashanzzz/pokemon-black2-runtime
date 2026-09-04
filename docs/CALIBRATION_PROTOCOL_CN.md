# Workbench v9 空间校准协议

建议每类至少录一次独立 session：

1. `outdoor_flat`：室外平地直走、转弯。
2. `door_transition`：门前静止 → 进门 → 室内静止 → 出门。
3. `indoor`：室内绕房间边缘走动。
4. `stairs`：楼梯/坡道上下至少各一次。
5. `bridge`：桥上走一段；如果可以到桥下，再单独录一段。
6. `building`：围绕一栋当前缺失/错位建筑走动。
7. `npc`：靠近一个 NPC，并从四个方向观察。

Workbench 校准录制过程中约每 450 ms 调用一次 calibration sample；样本来自已经存在的 PlayerRuntime / Scene cache，不触发 4 MiB full-RAM discovery。

## 通过条件

平地静止样本优先检查：

```text
WPos.x = GPos.x * 16 + 8
WPos.z = GPos.z * 16 + 8
```

移动插值期间不强制要求残差为 0。

Chunk 关系检查：`GPos // chunk_tile_size` 应与 Mapper.player_chunk 一致。

桥/楼梯重点检查：`GPos.y` 与 `WPos.y` 是否形成稳定、可重复的高度层变化。

## 报告用途

把 `calibration_*.zip` 发回后，可直接比较：

- 室内/室外是否使用同一 canonical transform
- 进入 Zone 后 player/scene 是否错层
- 某建筑是否 placement 正确但资源加载失败
- 某 NPC 是否 actor 存在但 OBJCODE/贴图映射失败
- 某桥是否需要多层节点而非二维碰撞格
