# v11 分层状态与战斗语义 API

本版本在现有 Pokémon Black 2 Runtime 上新增完整的分层状态协议、IREJ rev.1 战斗存在候选、战斗 API 骨架和新的首页。

## 直接打开

启动原有 runtime 后访问：

- `/` 新首页
- `/battle` 新首页的战斗入口
- `/workbench` 原逆向工作台
- `/docs` FastAPI OpenAPI

## 主要新增

- `GET /api/v1/game/current`
- `GET /api/v1/game/layers`
- `GET /api/v1/battle/state`
- `GET /api/v1/battle/request`
- `GET /api/v1/battle/field`
- `GET /api/v1/battle/party`
- `GET /api/v1/battle/moves`
- `GET /api/v1/battle/items`
- `GET /api/v1/battle/events`
- `GET /api/v1/battle/evidence`
- `POST /api/v1/battle/decisions`

旧 `/api/v1/battle/actions` 保留。

## 安全策略

当前只开放只读战斗存在候选。战斗动作仍然拒绝执行，直到 legal actions、目标、菜单定位和动作后验证完成。

完整设计见 `docs/GAME_STATE_AND_BATTLE_API_V2_CN.md`。
