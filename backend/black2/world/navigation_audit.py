"""Durable JSONL audit trails for navigation planning and execution.

Navigation is deliberately conservative, but failures can still be caused by
three different layers (renderer request, planner evidence, or BizHawk input).
Keeping one bounded JSON object per event makes those layers replayable after
the browser or backend has been restarted.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PLAN_LOG = PROJECT_ROOT / "logs" / "navigation_plans.jsonl"
DEFAULT_EXECUTION_LOG = PROJECT_ROOT / "logs" / "navigation_execution.jsonl"
_write_lock = Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any, *, depth: int = 0) -> Any:
    """Make log payloads JSON-safe without dumping arbitrary runtime data."""
    # Route nodes live under details -> segments -> actions -> from/to.  Keep
    # that complete diagnostic shape rather than replacing their coordinates
    # with depth markers; lists and keys remain bounded below.
    if depth > 9:
        return "<depth-limit>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key)[:120]: _safe(item, depth=depth + 1) for key, item in list(value.items())[:200]}
    if isinstance(value, (list, tuple, set)):
        return [_safe(item, depth=depth + 1) for item in list(value)[:500]]
    return str(value)[:500]


class NavigationAuditLog:
    def __init__(self, plan_path: Path = DEFAULT_PLAN_LOG, execution_path: Path = DEFAULT_EXECUTION_LOG) -> None:
        self.plan_path = Path(plan_path)
        self.execution_path = Path(execution_path)

    def record(self, kind: str, event: str, **details: Any) -> dict[str, Any]:
        path = self.plan_path if kind == "plan" else self.execution_path
        entry = {
            "timestamp_utc": _now(),
            "kind": kind,
            "event": str(event)[:120],
            "details": _safe(details),
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with _write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as output:
                output.write(line + "\n")
        return entry

    def recent(
        self, kind: str, *, limit: int = 100, task_id: str | None = None,
        plan_id: str | None = None,
    ) -> list[dict[str, Any]]:
        path = self.plan_path if kind == "plan" else self.execution_path
        bounded = max(1, min(int(limit), 500))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError):
            return []
        result: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(item, dict):
                continue
            details = item.get("details") or {}
            if task_id is not None and details.get("task_id") != task_id:
                continue
            if plan_id is not None and details.get("plan_id") != plan_id:
                continue
            result.append(item)
            if len(result) >= bounded:
                break
        return result


navigation_audit_log = NavigationAuditLog()
