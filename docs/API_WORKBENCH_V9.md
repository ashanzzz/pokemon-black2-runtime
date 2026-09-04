# Workbench v9 API

## GET `/api/v1/workbench/bootstrap`

用途：挂载整个 Workbench。

只返回：

- RuntimeHub health/cache
- RuntimeHub snapshot cache
- cached PlayerRuntime
- navigation/calibration status
- workspace/tool registry
- performance policy
- locale contract

禁止在此 endpoint 内执行 full RAM discovery。

## GET `/api/v1/workbench/events?limit=80`

合并：

- runtime lifecycle metadata
- dialogue timeline metadata

不包含 RAM bytes。

## GET `/api/v1/workbench/evidence`

返回：

- calibration status
- calibration reports
- observed navigation status
- evidence endpoint registry

## GET `/api/v1/workbench/versions`

统一组件版本视图。

## GET `/api/v1/workbench/schema`

Workbench UI 契约和 authoritative endpoint registry。

## 重型 API 不合并

以下能力仍要求显式调用：

- Player discovery
- Main RAM dump
- pattern scan
- write trace
- legacy BMD0 / BTX0 scan

这是 API 设计的一部分，不是缺陷。统一 UI 不应该把不同成本的读取藏在同一个“refresh”动作后面。
