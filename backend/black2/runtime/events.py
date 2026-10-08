"""Cursor-based event protocol for AI consumers of the runtime."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from .session_store import SessionStore


@dataclass(frozen=True)
class AgentEvent:
    event_id: str
    seq: int
    type: str
    observed_at: float
    frame: int | None
    session_id: str | None
    state_revision: int
    task_id: str | None
    plan_id: str | None
    correlation_id: str | None
    severity: str
    summary: str
    resources: dict[str, str]
    data: dict[str, Any]


class AgentEventBus:
    def __init__(
        self,
        max_events: int = 2048,
        persist_path: str | Path | None = None,
        session_store: SessionStore | None = None,
    ):
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._events: deque[AgentEvent] = deque(maxlen=max_events)
        self._max_events = max_events
        self._seq = 0
        self._state_revision = 0
        self._condition_obj: asyncio.Condition | None = None
        self._loop = None
        self.persist_path = Path(persist_path) if persist_path is not None else None
        self.session_store = session_store

    def _get_condition(self) -> asyncio.Condition:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._condition_obj is None or self._loop is not loop:
            self._condition_obj = asyncio.Condition()
            self._loop = loop
        return self._condition_obj
        self.persist_path = Path(persist_path) if persist_path is not None else None
        self.session_store = session_store

    def _persist(self, event: AgentEvent) -> None:
        """Persist event to SQLite SessionStore and optional ndjson log."""
        if self.session_store is not None:
            try:
                self.session_store.record_event(event)
            except Exception:
                pass

        if self.persist_path is None:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self.persist_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"format": "black2-agent-event/v1", **asdict(event)}, ensure_ascii=False) + "\n")
            if self.persist_path.stat().st_size > 8 * 1024 * 1024:
                lines = self.persist_path.read_text(encoding="utf-8").splitlines()[-self._max_events:]
                self.persist_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except OSError:
            return

    @property
    def cursor(self) -> int:
        return self._seq

    @property
    def state_revision(self) -> int:
        return self._state_revision

    @property
    def oldest_available(self) -> int:
        """The cursor immediately before the oldest retained event."""
        return self._events[0].seq - 1 if self._events else self._seq

    async def publish(
        self,
        event_type: str,
        *,
        frame: int | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        correlation_id: str | None = None,
        severity: str = "info",
        summary: str,
        resources: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        advances_state: bool = True,
    ) -> dict[str, Any]:
        async with self._get_condition():
            self._seq += 1
            if advances_state:
                self._state_revision += 1
            event = AgentEvent(
                event_id=f"evt_{uuid4().hex}",
                seq=self._seq,
                type=event_type,
                observed_at=time.time(),
                frame=frame,
                session_id=session_id,
                state_revision=self._state_revision,
                task_id=task_id,
                plan_id=plan_id,
                correlation_id=correlation_id,
                severity=severity,
                summary=summary,
                resources=resources or {},
                data=data or {},
            )
            self._events.append(event)
            self._persist(event)
            self._get_condition().notify_all()
            return {"format": "black2-agent-event/v1", **asdict(event)}

    def persisted_recent(self, limit: int = 100) -> dict[str, Any]:
        """Read the tail of the optional disk log for offline inspection."""
        limit = max(1, min(int(limit), self._max_events))
        if self.persist_path is None:
            return {"status": "disabled", "path": None, "events": []}
        try:
            lines = self.persist_path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            lines = []
        events: list[dict[str, Any]] = []
        for line in lines:
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                events.append(value)
        return {"status": "ok", "path": str(self.persist_path), "count": len(events), "events": events}

    def read_since(self, after: int, limit: int = 100) -> dict[str, Any]:
        if type(after) is not int or after < 0:
            raise ValueError("after must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 2048:
            raise ValueError("limit must be between 1 and 2048")
        oldest = self.oldest_available
        if after < oldest:
            return {
                "status": "cursor_expired",
                "requested_after": after,
                "oldest_available": oldest,
                "latest": self._seq,
                "recovery": {"state": "/api/v1/agent/state"},
            }
        events = [asdict(event) | {"format": "black2-agent-event/v1"}
                  for event in self._events if event.seq > after][:limit]
        return {
            "status": "ok",
            "events": events,
            "next_cursor": events[-1]["seq"] if events else after,
            "timed_out": False,
        }

    def since(self, after: int, limit: int = 100) -> list[dict[str, Any]]:
        """Compatibility list API; callers needing loss detection use read_since."""
        payload = self.read_since(after, limit)
        return payload.get("events", []) if payload.get("status") == "ok" else []

    async def wait_since_checked(
        self, after: int, *, timeout: float = 25.0, limit: int = 100,
    ) -> dict[str, Any]:
        initial = self.read_since(after, limit)
        if initial.get("status") != "ok" or initial.get("events"):
            return initial
        async with self._get_condition():
            try:
                await asyncio.wait_for(
                    self._get_condition().wait_for(lambda: self._seq > after),
                    timeout=max(0.0, timeout),
                )
            except asyncio.TimeoutError:
                return self.read_since(after, limit) | {"timed_out": True}
        return self.read_since(after, limit)

    async def wait_since(
        self, after: int, *, timeout: float = 25.0, limit: int = 100,
    ) -> list[dict[str, Any]]:
        payload = await self.wait_since_checked(after, timeout=timeout, limit=limit)
        return payload.get("events", []) if payload.get("status") == "ok" else []

    def query_history(
        self,
        since_seq: int = 0,
        limit: int = 100,
        session_id: str | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query persisted event history from SQLite SessionStore if configured."""
        if self.session_store is not None:
            return self.session_store.get_events(
                since_seq=since_seq,
                limit=limit,
                session_id=session_id,
                event_type=event_type,
            )
        return []


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SESSION_DB = _PROJECT_ROOT / "runtime" / "session.db"
session_store = SessionStore(_DEFAULT_SESSION_DB)

_EVENT_LOG_PATH = _PROJECT_ROOT / "runtime" / "ai_context" / "agent_events.ndjson"
agent_event_bus = AgentEventBus(persist_path=_EVENT_LOG_PATH, session_store=session_store)
