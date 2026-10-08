"""Precondition-gated semantic actions for safe asynchronous execution."""
from __future__ import annotations

import asyncio
import copy
import inspect
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable
from uuid import uuid4

from ..runtime.events import agent_event_bus
from .input_lease import InputLease


ACTIVE = {"accepted", "waiting_precondition", "ready", "executing"}
TERMINAL = {"completed", "rejected", "stale", "failed", "cancelled"}


@dataclass
class PreparedActionRecord:
    action_id: str
    kind: str
    command: dict[str, Any]
    status: str
    created_at: float
    correlation_id: str | None
    expected_session_id: str | None
    expected_state_revision: int | None
    expected_primary_mode: str | None
    expected_request_id: str | None
    expected_battle_id: str | None
    expected_dialogue_instance_id: str | None
    expected_task_id: str | None
    expected_actor_id: str | None
    execute_when: dict[str, Any]
    event_cursor: int
    expires_at_frame: int | None
    expires_at: float | None
    result: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None


def _nested(state: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = state
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value is not None:
            return value
    return None


class PreparedActionService:
    """Keep semantic plans dormant until their stable identity still matches."""

    def __init__(
        self,
        *,
        state_provider: Callable[[], dict[str, Any]],
        executor: Callable[[dict[str, Any]], Any],
        event_sink: Callable[..., Any] | None = None,
        event_cursor: Callable[[], int] | None = None,
        lease: InputLease | None = None,
        poll_seconds: float = 0.05,
    ) -> None:
        self.state_provider = state_provider
        self.executor = executor
        self.event_sink = event_sink or agent_event_bus.publish
        self.event_cursor = event_cursor or (lambda: agent_event_bus.cursor)
        self.lease = lease or InputLease()
        self.poll_seconds = max(0.01, float(poll_seconds))
        self._records: dict[str, PreparedActionRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._worker: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _state_value(state: dict[str, Any], field: str) -> Any:
        paths = {
            "session_id": (("session_id",), ("transport", "session_id")),
            "frame": (("frame",), ("transport", "frame")),
            "state_revision": (("state_revision",), ("semantic", "state_revision")),
            "primary_mode": (("primary_mode",), ("semantic", "context", "screen_type")),
            "request_id": (
                ("request_id",), ("battle", "request_id"), ("battle", "request", "request_id"),
            ),
            "battle_id": (("battle_id",), ("battle", "battle_id"), ("battle", "request", "battle_id")),
            "dialogue_instance_id": (
                ("dialogue_instance_id",), ("dialogue", "instance_id"),
                ("semantic", "context", "dialogue_instance_id"),
            ),
            "task_id": (("task_id",), ("active_task", "task_id")),
            "actor_id": (("actor_id",), ("player", "actor_id")),
        }
        return _nested(state, *paths.get(field, ((field,),)))

    @staticmethod
    def _reject_raw_command(kind: str, command: dict[str, Any], execute_when: dict[str, Any]) -> None:
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("kind must identify a semantic action")
        if kind.strip().lower() in {"raw", "raw_input", "button_sequence"}:
            raise ValueError("raw button queues are not valid prepared semantic actions")
        if not isinstance(command, dict) or not command:
            raise ValueError("command must be a non-empty semantic command object")
        if (
            any(key in command for key in ("buttons", "button", "keys"))
            or command.get("type") in {"raw_buttons", "press_buttons", "button_sequence"}
        ):
            raise ValueError("raw button queues are not valid prepared semantic actions")
        if not isinstance(execute_when, dict):
            raise ValueError("execute_when must be an object")
        if "event" in execute_when and not isinstance(execute_when["event"], str):
            raise ValueError("execute_when.event must be a string")

    def _emit(self, event_type: str, record: PreparedActionRecord, *, summary: str, data: dict[str, Any] | None = None) -> None:
        try:
            result = self.event_sink(
                event_type,
                correlation_id=record.correlation_id,
                summary=summary,
                resources={"action": f"/api/v1/agent/actions/{record.action_id}"},
                data=data or {},
            )
            if inspect.isawaitable(result):
                asyncio.create_task(result)
        except Exception:
            pass

    @staticmethod
    def _public(record: PreparedActionRecord) -> dict[str, Any]:
        result = asdict(record)
        result["format"] = "black2-prepared-action/v1"
        result["watch"] = {
            "event_cursor": record.event_cursor,
            "resource": f"/api/v1/agent/actions/{record.action_id}",
        }
        return result

    def get(self, action_id: str) -> dict[str, Any]:
        record = self._records.get(action_id)
        if record is None:
            raise KeyError(action_id)
        return self._public(record)

    async def submit(
        self,
        *,
        kind: str,
        command: dict[str, Any],
        preconditions: dict[str, Any] | None = None,
        execute_when: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        ttl_frames: int | None = None,
        ttl_seconds: float | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        preconditions = preconditions or {}
        execute_when = execute_when or {}
        self._reject_raw_command(kind, command, execute_when)
        if not isinstance(preconditions, dict):
            raise ValueError("preconditions must be an object")
        if preconditions.get("session_id") is None or preconditions.get("primary_mode") is None:
            raise ValueError("preconditions must include session_id and primary_mode")
        if ttl_frames is not None and (isinstance(ttl_frames, bool) or not 1 <= ttl_frames <= 100000):
            raise ValueError("ttl_frames must be between 1 and 100000")
        if ttl_seconds is not None and (isinstance(ttl_seconds, bool) or not 0 < ttl_seconds <= 3600):
            raise ValueError("ttl_seconds must be between 0 and 3600")
        if idempotency_key:
            prior_id = self._idempotency.get(idempotency_key)
            if prior_id is not None:
                return self._public(self._records[prior_id]) | {"idempotent_replay": True}

        state = self.state_provider() or {}
        frame = self._state_value(state, "frame")
        frame = int(frame) if isinstance(frame, (int, float)) and not isinstance(frame, bool) else None
        record = PreparedActionRecord(
            action_id=f"act_{uuid4().hex}",
            kind=kind,
            command=copy.deepcopy(command),
            status="waiting_precondition",
            created_at=time.time(),
            correlation_id=correlation_id,
            expected_session_id=preconditions.get("session_id"),
            expected_state_revision=preconditions.get("state_revision"),
            expected_primary_mode=preconditions.get("primary_mode"),
            expected_request_id=preconditions.get("request_id"),
            expected_battle_id=preconditions.get("battle_id"),
            expected_dialogue_instance_id=preconditions.get("dialogue_instance_id"),
            expected_task_id=preconditions.get("task_id"),
            expected_actor_id=preconditions.get("actor_id"),
            execute_when=copy.deepcopy(execute_when),
            event_cursor=int(self.event_cursor()),
            expires_at_frame=(frame + ttl_frames) if frame is not None and ttl_frames is not None else None,
            expires_at=(time.time() + ttl_seconds) if ttl_seconds is not None else None,
        )
        self._records[record.action_id] = record
        if idempotency_key:
            self._idempotency[idempotency_key] = record.action_id
        self._emit("action.accepted", record, summary="Prepared semantic action accepted.")
        self._emit("action.waiting_precondition", record, summary="Prepared action is waiting for execution conditions.")
        self._ensure_worker()
        return self._public(record)

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="black2-prepared-actions")

    def _validate_preconditions(self, record: PreparedActionRecord, state: dict[str, Any]) -> tuple[bool, str | None]:
        for field, expected in (
            ("session_id", record.expected_session_id),
            ("primary_mode", record.expected_primary_mode),
            ("request_id", record.expected_request_id),
            ("battle_id", record.expected_battle_id),
            ("dialogue_instance_id", record.expected_dialogue_instance_id),
            ("task_id", record.expected_task_id),
            ("actor_id", record.expected_actor_id),
        ):
            if expected is None:
                continue
            actual = self._state_value(state, field)
            if actual is None:
                return False, "precondition_unresolved"
            if actual != expected:
                return False, f"{field}_changed"
        if record.expires_at is not None and time.time() > record.expires_at:
            return False, "expired"
        if record.expires_at_frame is not None:
            frame = self._state_value(state, "frame")
            if isinstance(frame, (int, float)) and frame > record.expires_at_frame:
                return False, "expired"
        return True, None

    def _event_ready(self, record: PreparedActionRecord) -> bool:
        event_name = record.execute_when.get("event")
        if not event_name:
            return True
        payload = agent_event_bus.read_since(record.event_cursor, 2048)
        return payload.get("status") == "ok" and any(item.get("type") == event_name for item in payload.get("events", []))

    async def _stale(self, record: PreparedActionRecord, reason: str) -> None:
        record.status = "stale"
        record.failure = {"code": "ACTION_STALE", "reason": reason}
        self._emit("action.stale", record, summary="Prepared action discarded because its preconditions changed.", data={"reason": reason})

    async def _run_one(self, record: PreparedActionRecord, state: dict[str, Any]) -> None:
        valid, reason = self._validate_preconditions(record, state)
        if not valid:
            if reason == "precondition_unresolved":
                return
            await self._stale(record, reason or "precondition_changed")
            return
        if not self._event_ready(record):
            return
        record.status = "ready"
        self._emit("action.ready", record, summary="Prepared action is ready after precondition validation.")
        async with self.lease.acquire(owner_kind="prepared_action", owner_id=record.action_id):
            # The state can change while waiting for the shared input lease.
            latest = self.state_provider() or {}
            valid, reason = self._validate_preconditions(record, latest)
            if not valid:
                if reason == "precondition_unresolved":
                    record.status = "waiting_precondition"
                    return
                await self._stale(record, reason or "precondition_changed_before_execution")
                return
            if not self._event_ready(record):
                record.status = "waiting_precondition"
                return
            record.status = "executing"
            self._emit("action.started", record, summary="Prepared semantic action execution started.")
            try:
                result = self.executor(copy.deepcopy(record.command))
                if inspect.isawaitable(result):
                    result = await result
                record.result = result if isinstance(result, dict) else {"value": result}
                record.status = "completed"
                self._emit("action.completed", record, summary="Prepared semantic action completed.", data=record.result)
            except Exception as exc:
                record.status = "failed"
                record.failure = {"code": "ACTION_EXECUTION_FAILED", "error": f"{type(exc).__name__}: {exc}"}
                self._emit("action.failed", record, summary="Prepared semantic action failed.", data=record.failure)

    async def _run(self) -> None:
        while True:
            active = [record for record in self._records.values() if record.status in ACTIVE]
            if not active:
                return
            for record in active:
                try:
                    await self._run_one(record, self.state_provider() or {})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    record.status = "failed"
                    record.failure = {"code": "ACTION_INTERNAL", "error": f"{type(exc).__name__}: {exc}"}
                    self._emit("action.failed", record, summary="Prepared action worker failed.", data=record.failure)
            await asyncio.sleep(self.poll_seconds)

    async def cancel(self, action_id: str) -> dict[str, Any]:
        record = self._records.get(action_id)
        if record is None:
            raise KeyError(action_id)
        if record.status in TERMINAL:
            return self._public(record)
        record.status = "cancelled"
        record.failure = {"code": "ACTION_CANCELLED", "reason": "cancelled_by_client"}
        self._emit("action.cancelled", record, summary="Prepared action cancelled by client.")
        return self._public(record)

    async def invalidate_session(self, previous: str | None, current: str | None) -> None:
        for record in list(self._records.values()):
            if record.status not in ACTIVE:
                continue
            await self._stale(record, "session_changed")
