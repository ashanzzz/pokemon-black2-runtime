# v8 发布包清理

从用户提供的约 1.3 GB 工作目录中，发布版不携带以下运行/临时内容：

- `.venv/`
- `runtime/` 缓存
- `logs/` 与 `*.log`
- `__pycache__/`、`.pytest_cache/`
- `reverse_engineering/dumps/` 原始大型 RAM dumps
- `reverse_engineering/v6_evidence/` 历史截图包
- `*.v4bak` / `*.v6bak` / `*.v7bak`
- 旧地图前端：`native-map.html`、`map-runtime.html`、`navigation.html` 及其专用 renderer
- 与本项目无关的 pi coding-agent package/docs/theme/export-html 资源

保留 `reverse_engineering/derived/v5/`，因为它只有约十余 MB，是 ROM 静态世界的有效派生缓存，可显著减少运行时重复解析。

原始 evidence 不应丢弃，但建议独立归档，不与可执行源码 Git ZIP 混在一起。
