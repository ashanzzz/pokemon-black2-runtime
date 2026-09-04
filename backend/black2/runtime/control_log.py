"""Persistent, metadata-only audit log for the local runtime supervisor.

This module intentionally records service lifecycle facts only.  It must never
be used for RAM samples, decoded gameplay state, or restart credentials.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from .versions import RUNTIME_RELEASE_VERSION


RUNTIME_MONITOR_VERSION = RUNTIME_RELEASE_VERSION
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG_PATH = PROJECT_ROOT / "logs" / "runtime_control.jsonl"
_write_lock = Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_details(details: dict[str, Any]) -> dict[str, Any]:
    """Keep lifecycle metadata compact and reject payload-shaped secrets/data."""
    safe: dict[str, Any] = {}
    blocked = {"memory", "ram", "bytes", "payload", "restart_token", "token", "secret"}
    for key, value in details.items():
        if key.lower() in blocked:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, tuple)):
            safe[key] = [str(item)[:120] for item in value[:12]]
        else:
            safe[key] = str(value)[:300]
    return safe


class RuntimeControlLog:
    """A small JSONL journal that remains readable after replacement restart."""

    def __init__(self, path: Path = DEFAULT_LOG_PATH) -> None:
        self.path = path

    def record(self, operation: str, result: str, **details: Any) -> dict[str, Any]:
        entry = {
            "timestamp_utc": _utc_now(),
            "component": "runtime-monitor",
            "version": RUNTIME_MONITOR_VERSION,
            "pid": os.getpid(),
            "operation": str(operation)[:96],
            "result": str(result)[:96],
            "details": _safe_details(details),
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with _write_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(line + "\n")
        return entry

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in lines[-bounded:]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                entries.append(item)
        return list(reversed(entries))


runtime_control_log = RuntimeControlLog()
