# Black 2 离线图鉴 API

服务启动后，图鉴接口只读取仓库内的
`backend/black2/dex/data/black2_dex_v1.json.gz`，运行时不访问网络。数据由
`tools/build_black2_dex.py` 从固定的 PokeAPI CSV commit 生成。

## 入口

`GET /api/v1/dex/summary` 返回数据版本、来源 commit、Gen5 版本组、ROM
provenance、ID 空间和计数。当前范围为全国图鉴 1-649、招式 1-559、17
种属性、Gen5 游戏索引道具和第五世代主系列特性。

`/api/v1/dex/{pokemon,moves,items,abilities,types}` 支持 `q`、`limit`、
`offset` 列表查询；追加 `/{id}` 获取完整详情。道具还支持
`game_index` 筛选和 `/items/by-game-index/{index}` 反查。返回的 `game_indices`
会保留 PokeAPI 中的多候选关系，`game_index_ambiguous=true` 时调用方必须
等待运行时上下文进一步消歧。

为旧客户端提供统一入口：

* `/api/v1/catalog/capabilities`
* `/api/v1/catalog/{pokemon,moves,items,abilities,types}` 及详情路径
* `/api/v1/catalog/search?q=...`，可用 `entity` 过滤实体类型
* `/api/v1/catalog/rom/{kind}/{rom_id}`，按 Gen5/ROM-facing id 反查候选

所有列表响应都带 `dataset`、`pagination.matched` 和 `pagination.next_offset`；
详情响应同时提供实体包装字段（例如 `pokemon` 或 `move`）和顶层实体字段，
方便严格客户端与轻量脚本渐进迁移。

## 时间范围和来源

招式数值使用 version group 14（Black 2/White 2），并按 PokeAPI changelog
回溯后世改动；宝可梦默认形态的属性、种族值和特性使用第五世代历史表。
道具保留 Gen5 `game_indices`，并在 PokeAPI 提供时附上 version-group-14
价格。招式 `move_meta` 和道具 prose 在源表没有完整历史版本时会标明
`temporal_scope`，不会伪装成 ROM 精确值。

ROM 本体不随仓库打包；`rom_provenance` 说明关联字段和外部本地 ROM 资源，
因此图鉴事实与运行时 RAM/ROM 观测可以在调用方日志中明确区分。
