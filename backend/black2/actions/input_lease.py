"""Shared ownership guard for every emulator input executor."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


class InputLease:
    """Serialize emulator input and expose the current owner for diagnostics (Re-entrant)."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.owner: dict[str, Any] | None = None
        self._owner_task: asyncio.Task | None = None
        self._depth: int = 0

    @asynccontextmanager
    async def acquire(self, *, owner_kind: str, owner_id: str) -> AsyncIterator[None]:
        current_task = asyncio.current_task()
        if self._owner_task is not None and self._owner_task == current_task:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
                if self._depth == 0:
                    self.owner = None
                    self._owner_task = None
            return

        await self.lock.acquire()
        self._owner_task = current_task
        self._depth = 1
        self.owner = {"kind": owner_kind, "id": owner_id}
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0:
                self.owner = None
                self._owner_task = None
                self.lock.release()

    async def __aenter__(self) -> "InputLease":
        current_task = asyncio.current_task()
        if self._owner_task is not None and self._owner_task == current_task:
            self._depth += 1
            return self
        await self.lock.acquire()
        self._owner_task = current_task
        self._depth = 1
        self.owner = {"kind": "legacy", "id": "unknown"}
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._depth -= 1
        if self._depth == 0:
            self.owner = None
            self._owner_task = None
            self.lock.release()

input_lease = InputLease()
