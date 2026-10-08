"""Transactional CommandBus with InputLease, Pre/Post Verification, and Rollback.

Every mutating operation (party swap, teach move, PC deposit/withdraw/swap)
must execute through CommandBus to guarantee:
1. Re-entrant InputLease acquisition (prevents concurrency conflicts)
2. Pre-state snapshot capture (for rollback safety)
3. Verified post-condition check (checksum, count, integrity)
4. Automatic rollback to pre-state if post-check fails or on error.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple
from dataclasses import dataclass, field

from .input_lease import InputLease
from ..memory.reader import MemoryReader


@dataclass
class TransactionResult:
    ok: bool
    status: str
    command_name: str
    transaction_id: str
    elapsed_ms: float
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    rolled_back: bool = False


class CommandBus:
    """Unified transactional execution engine for mutating gameplay operations."""

    def __init__(self, lease: InputLease | None = None) -> None:
        self.lease = lease or InputLease()

    async def execute_transaction(
        self,
        command_name: str,
        *,
        owner_id: str,
        # Callback to capture pre-state snapshot bytes: returns (ram_offset, snapshot_bytes, metadata)
        capture_pre_state: Callable[[], Awaitable[Tuple[int, bytes, Dict[str, Any]]]],
        # Mutation function: takes pre_metadata, performs write or action
        execute_action: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        # Callback to verify post-state: returns (is_valid: bool, verification_data: dict, error_message: str | None)
        verify_post_state: Callable[[Dict[str, Any]], Awaitable[Tuple[bool, Dict[str, Any], Optional[str]]]],
        # Rollback writer: writes bytes to RAM offset
        write_ram: Callable[[int, bytes], Awaitable[Any]],
    ) -> TransactionResult:
        tx_id = f"tx_{command_name}_{uuid.uuid4().hex[:8]}"
        t0 = time.monotonic()

        async with self.lease.acquire(owner_kind="command_bus", owner_id=f"{command_name}:{owner_id}"):
            # 1. Pre-state capture
            try:
                ram_offset, pre_snapshot, pre_meta = await capture_pre_state()
            except Exception as exc:
                elapsed = (time.monotonic() - t0) * 1000
                return TransactionResult(
                    ok=False,
                    status="pre_check_failed",
                    command_name=command_name,
                    transaction_id=tx_id,
                    elapsed_ms=round(elapsed, 2),
                    error=f"Pre-condition snapshot failed: {type(exc).__name__}: {exc}",
                )

            # 2. Execute mutation
            try:
                action_result = await execute_action(pre_meta)
            except Exception as exc:
                # Execution raised exception, restore pre-state
                rolled_back = False
                try:
                    await write_ram(ram_offset, pre_snapshot)
                    rolled_back = True
                except Exception:
                    pass
                elapsed = (time.monotonic() - t0) * 1000
                return TransactionResult(
                    ok=False,
                    status="execution_error",
                    command_name=command_name,
                    transaction_id=tx_id,
                    elapsed_ms=round(elapsed, 2),
                    error=f"Execution failed: {type(exc).__name__}: {exc}",
                    rolled_back=rolled_back,
                )

            # 3. Post-condition verification
            try:
                is_valid, post_meta, error_msg = await verify_post_state(action_result)
            except Exception as exc:
                is_valid = False
                post_meta = {}
                error_msg = f"Post-verification check raised {type(exc).__name__}: {exc}"

            if not is_valid:
                # Post-condition verification failed: automatic rollback!
                rolled_back = False
                try:
                    await write_ram(ram_offset, pre_snapshot)
                    rolled_back = True
                except Exception:
                    pass
                elapsed = (time.monotonic() - t0) * 1000
                return TransactionResult(
                    ok=False,
                    status="post_check_failed_rolled_back",
                    command_name=command_name,
                    transaction_id=tx_id,
                    elapsed_ms=round(elapsed, 2),
                    error=f"Post-verification failed: {error_msg}. Memory snapshot rolled back.",
                    rolled_back=rolled_back,
                    data={"action_result": action_result, "post_meta": post_meta},
                )

            # 4. Transaction succeeded
            elapsed = (time.monotonic() - t0) * 1000
            return TransactionResult(
                ok=True,
                status="succeeded",
                command_name=command_name,
                transaction_id=tx_id,
                elapsed_ms=round(elapsed, 2),
                data={**action_result, **post_meta},
            )


# Global default instance
command_bus = CommandBus()
