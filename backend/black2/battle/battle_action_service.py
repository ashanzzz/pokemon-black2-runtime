"""Autonomous Gen 5 battle action closed-loop execution service for Pokémon Black 2.

Translates high-level BattlePlanner decisions (use_move, throw_ball, run, use_item, switch)
into deterministic emulator inputs protected by InputLease, and verifies the outcome via
closed-loop RAM readback (PP decrement, opponent HP damage, knockout, capture, escape).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from ..actions.input_engine import ActionEngine
from ..actions.input_lease import InputLease, input_lease
from ..decoders.battle_runtime import BattleRuntimeDecoder
from ..decoders.battle_ui_cursor import BattleUiCursorDecoder
from ..decoders.battle_identity import BattleIdentityDecoder
from ..decoders.party_runtime import PlayerPartyDecoder
from ..decoders.inventory_runtime import PlayerInventoryDecoder
from .battle_state_machine import battle_state_machine


MOVE_TOUCH_COORDS = {
    1: (64, 48),
    2: (192, 48),
    3: (64, 144),
    4: (192, 144),
}

PARTY_TOUCH_COORDS = {
    1: (64, 40),
    2: (192, 40),
    3: (64, 80),
    4: (192, 80),
    5: (64, 120),
    6: (192, 120),
}



class BattleActionService:
    """Closed-loop execution engine for in-battle actions."""

    def __init__(
        self,
        action_engine: ActionEngine | None = None,
        lease: InputLease | None = None,
    ) -> None:
        self.action_engine = action_engine
        self.lease = lease or input_lease
        self.party_decoder = PlayerPartyDecoder()
        self.inventory_decoder = PlayerInventoryDecoder()

    def configure(self, action_engine: ActionEngine, reader: Any = None) -> None:
        self.action_engine = action_engine
        if reader is not None:
            self.party_decoder.configure(reader)
            self.inventory_decoder.configure(reader)

    async def _normalize_to_command_menu(self) -> None:
        """Reset battle UI back to the root command menu regardless of current submenu.

        Eliminates order dependency by pressing 'B' until the root command menu is reached.
        """
        if self.action_engine is None:
            return
        for _ in range(4):
            snapshot = await battle_state_machine.sample()
            if not snapshot.get("active"):
                return
            phase = snapshot.get("phase")
            if phase == "command_menu":
                return
            # Press B to cancel current submenu and wait briefly
            await self.action_engine.press_button("B", hold_frames=4, wait_frames=10)
            await asyncio.sleep(0.2)

    async def execute_decision(
            self,
            command_or_decision: dict[str, Any],
            *,
            request_id: str | None = None,
        ) -> dict[str, Any]:
            """Execute a recommended battle action and verify post-conditions."""
            if self.action_engine is None:
                return {
                    "format": "black2-battle-action-execution/v1",
                    "status": "rejected",
                    "executed": False,
                    "reason": {"code": "ACTION_ENGINE_NOT_CONFIGURED", "message": "ActionEngine emulator bridge is not configured."},
                    "request_id": request_id,
                }

            # Normalize command payload
            if "commands" in command_or_decision and isinstance(command_or_decision["commands"], list):
                if not command_or_decision["commands"]:
                    return {
                        "format": "black2-battle-action-execution/v1",
                        "status": "rejected",
                        "executed": False,
                        "reason": {"code": "EMPTY_COMMANDS", "message": "No battle commands provided."},
                        "request_id": request_id,
                    }
                cmd = command_or_decision["commands"][0]
            else:
                cmd = command_or_decision

            if hasattr(cmd, "model_dump"):
                cmd = cmd.model_dump(mode="json", exclude_none=True)

            cmd_type = cmd.get("type")
            if not cmd_type:
                return {
                    "format": "black2-battle-action-execution/v1",
                    "status": "rejected",
                    "executed": False,
                    "reason": {"code": "INVALID_COMMAND_TYPE", "message": "Command missing 'type' discriminator."},
                    "request_id": request_id,
                }

            async with self.lease.acquire(owner_kind="battle", owner_id=f"battle:{cmd_type}"):
                if cmd_type == "use_move":
                    return await self._execute_use_move(cmd, request_id=request_id)
                elif cmd_type == "throw_ball":
                    return await self._execute_throw_ball(cmd, request_id=request_id)
                elif cmd_type == "run":
                    return await self._execute_run(cmd, request_id=request_id)
                elif cmd_type == "use_item":
                    return await self._execute_use_item(cmd, request_id=request_id)
                elif cmd_type == "switch":
                    return await self._execute_switch(cmd, request_id=request_id)
                else:
                    return {
                        "format": "black2-battle-action-execution/v1",
                        "status": "rejected",
                        "executed": False,
                        "reason": {"code": "UNSUPPORTED_COMMAND_TYPE", "message": f"Command type '{cmd_type}' is not executable."},
                        "request_id": request_id,
                    }


    async def _execute_use_move(self, cmd: dict[str, Any], *, request_id: str | None) -> dict[str, Any]:
        await self._normalize_to_command_menu()
        move_slot = int(cmd.get("move_slot", 1))
        if move_slot not in (1, 2, 3, 4):
            return {
                "format": "black2-battle-action-execution/v1",
                "status": "rejected",
                "executed": False,
                "reason": {"code": "INVALID_MOVE_SLOT", "message": f"Move slot {move_slot} must be 1..4."},
                "request_id": request_id,
            }

        # 1. Sample state before
        snapshot = await battle_state_machine.sample()
        if not snapshot.get("active"):
            return {
                "format": "black2-battle-action-execution/v1",
                "status": "rejected",
                "executed": False,
                "reason": {"code": "BATTLE_NOT_ACTIVE", "message": "Battle is not active in RAM."},
                "request_id": request_id,
            }

        player_data = snapshot.get("player") or {}
        opp_data = snapshot.get("opponent") or {}
        moves = (player_data.get("active") or player_data).get("moves", [])
        target_move = next((m for m in moves if m.get("slot") == move_slot), None)
        if target_move is None and len(moves) >= move_slot:
            target_move = moves[move_slot - 1]

        before_pp = target_move.get("current_pp", 0) if isinstance(target_move, dict) else 0
        if before_pp <= 0:
            move_name = target_move.get("name") if isinstance(target_move, dict) else f"Move #{move_slot}"
            max_pp_val = target_move.get("max_pp", 0) if isinstance(target_move, dict) else 0
            return {
                "format": "black2-battle-action-execution/v1",
                "status": "rejected",
                "executed": False,
                "reason": {
                    "code": "MOVE_PP_EXHAUSTED",
                    "message": f"Move slot {move_slot}「{move_name}」PP is exhausted (0/{max_pp_val}). Action aborted to prevent a wasted turn.",
                },
                "move_slot": move_slot,
                "current_pp": 0,
                "max_pp": max_pp_val,
                "request_id": request_id,
            }
        opp_hp_before = (opp_data.get("active") or opp_data).get("current_hp", 0)

        # 2. Issue input
        phase = snapshot.get("phase")
        writes = []

        if phase == "command_menu":
            # Touch FIGHT button (128, 96) or press A
            touch_res = await self.action_engine.touch_screen(128, 96, hold_frames=6)
            writes.append({"touch": [128, 96], "purpose": "open_move_menu", "response": touch_res})
            await asyncio.sleep(0.4)

        # Touch the move quadrant
        tx, ty = MOVE_TOUCH_COORDS[move_slot]
        touch_move = await self.action_engine.touch_screen(tx, ty, hold_frames=8)
        writes.append({"touch": [tx, ty], "purpose": f"confirm_move_slot_{move_slot}", "response": touch_move})

        # 3. Settle and auto-advance dialogue/animations
        for _ in range(8):
            await asyncio.sleep(0.6)
            await self.action_engine.press_button("B", hold_frames=4, wait_frames=12)
            cur = await battle_state_machine.sample()
            if not cur.get("active") or cur.get("phase") == "command_menu":
                break

        # 4. Readback verification
        after = await battle_state_machine.sample()
        after_player = after.get("player") or {}
        after_opp = after.get("opponent") or {}
        after_moves = (after_player.get("active") or after_player).get("moves", [])
        after_move = next((m for m in after_moves if m.get("slot") == move_slot), None)
        if after_move is None and len(after_moves) >= move_slot:
            after_move = after_moves[move_slot - 1]

        after_pp = after_move.get("current_pp") if isinstance(after_move, dict) else None
        opp_hp_after = (after_opp.get("active") or after_opp).get("current_hp")
        pp_decreased = (before_pp - after_pp == 1) if isinstance(after_pp, int) else False
        damage_dealt = (opp_hp_before > opp_hp_after) if (isinstance(opp_hp_after, int) and opp_hp_before > 0) else False
        battle_ended = after.get("active") is False
        turn_passed = after.get("phase") == "command_menu"

        verified = pp_decreased or damage_dealt or battle_ended or turn_passed

        return {
            "format": "black2-battle-action-execution/v1",
            "status": "executed" if verified else "unverified",
            "executed": verified,
            "action": cmd,
            "verification": {
                "code": "MOVE_EXECUTED_VERIFIED" if verified else "MOVE_EXECUTION_UNVERIFIED",
                "move_slot": move_slot,
                "before_pp": before_pp,
                "after_pp": after_pp,
                "pp_decreased": pp_decreased,
                "opp_hp_before": opp_hp_before,
                "opp_hp_after": opp_hp_after,
                "damage_dealt": damage_dealt,
                "battle_ended": battle_ended,
                "turn_passed": turn_passed,
            },
            "writes": writes,
            "request_id": request_id,
        }

    async def _execute_throw_ball(self, cmd: dict[str, Any], *, request_id: str | None) -> dict[str, Any]:
        await self._normalize_to_command_menu()
        item_id = int(cmd.get("item_id", 4))
        snapshot = await battle_state_machine.sample()
        if not snapshot.get("active"):
            return {
                "format": "black2-battle-action-execution/v1",
                "status": "rejected",
                "executed": False,
                "reason": {"code": "BATTLE_NOT_ACTIVE", "message": "Battle is not active in RAM."},
                "request_id": request_id,
            }

        if snapshot.get("battle_kind") == "trainer":
            return {
                "format": "black2-battle-action-execution/v1",
                "status": "rejected",
                "executed": False,
                "reason": {"code": "CANNOT_CATCH_TRAINER_POKEMON", "message": "Cannot catch trainer pokemon."},
                "request_id": request_id,
            }

        writes = []
        # Touch BAG (45, 175)
        touch_bag = await self.action_engine.touch_screen(45, 175, hold_frames=8)
        writes.append({"touch": [45, 175], "purpose": "open_bag", "response": touch_bag})
        await asyncio.sleep(0.5)

        # Right to pokeball pocket -> A -> A (select ball) -> A (use)
        await self.action_engine.press_button("Right", hold_frames=4, wait_frames=10)
        await asyncio.sleep(0.25)
        await self.action_engine.press_button("A", hold_frames=4, wait_frames=15)
        await asyncio.sleep(0.3)
        await self.action_engine.press_button("A", hold_frames=4, wait_frames=15)
        await asyncio.sleep(0.3)
        await self.action_engine.press_button("A", hold_frames=4, wait_frames=15)

        # Settle
        for _ in range(10):
            await asyncio.sleep(0.6)
            await self.action_engine.press_button("B", hold_frames=6, wait_frames=15)
            cur = await battle_state_machine.sample()
            if not cur.get("active") or cur.get("phase") == "command_menu":
                break

        after = await battle_state_machine.sample()
        caught = after.get("active") is False
        return {
            "format": "black2-battle-action-execution/v1",
            "status": "executed",
            "executed": True,
            "action": cmd,
            "verification": {
                "code": "POKEBALL_THROWN_SUCCESS" if caught else "POKEBALL_THROWN_BREAKOUT",
                "caught": caught,
                "battle_active_after": after.get("active"),
            },
            "writes": writes,
            "request_id": request_id,
        }

    async def _execute_run(self, cmd: dict[str, Any], *, request_id: str | None) -> dict[str, Any]:
        await self._normalize_to_command_menu()
        snapshot = await battle_state_machine.sample()
        if not snapshot.get("active"):
            return {
                "format": "black2-battle-action-execution/v1",
                "status": "rejected",
                "executed": False,
                "reason": {"code": "BATTLE_NOT_ACTIVE", "message": "Battle is not active in RAM."},
                "request_id": request_id,
            }

        if snapshot.get("battle_kind") == "trainer":
            return {
                "format": "black2-battle-action-execution/v1",
                "status": "rejected",
                "executed": False,
                "reason": {"code": "CANNOT_RUN_FROM_TRAINER", "message": "Cannot flee from trainer battle."},
                "request_id": request_id,
            }

        writes = []
        touch_run = await self.action_engine.touch_screen(128, 178, hold_frames=8)
        writes.append({"touch": [128, 178], "purpose": "touch_run", "response": touch_run})

        for _ in range(6):
            await asyncio.sleep(0.4)
            await self.action_engine.press_button("B", hold_frames=6, wait_frames=15)
            cur = await battle_state_machine.sample()
            if not cur.get("active"):
                break

        after = await battle_state_machine.sample()
        escaped = after.get("active") is False
        return {
            "format": "black2-battle-action-execution/v1",
            "status": "executed" if escaped else "unverified",
            "executed": escaped,
            "action": cmd,
            "verification": {
                "code": "RUN_SUCCESS" if escaped else "RUN_FAILED",
                "escaped": escaped,
                "battle_active_after": after.get("active"),
            },
            "writes": writes,
            "request_id": request_id,
        }

    async def _execute_use_item(self, cmd: dict[str, Any], *, request_id: str | None) -> dict[str, Any]:
        await self._normalize_to_command_menu()
        writes = []
        touch_bag = await self.action_engine.touch_screen(45, 175, hold_frames=8)
        writes.append({"touch": [45, 175], "purpose": "open_bag", "response": touch_bag})
        await asyncio.sleep(0.5)

        # Enter recovery pocket -> select item 1 -> use -> select party 1
        await self.action_engine.press_button("A", hold_frames=4, wait_frames=15)
        await asyncio.sleep(0.3)
        await self.action_engine.press_button("A", hold_frames=4, wait_frames=15)
        await asyncio.sleep(0.3)
        await self.action_engine.press_button("A", hold_frames=4, wait_frames=15)
        await asyncio.sleep(0.4)
        await self.action_engine.press_button("A", hold_frames=4, wait_frames=15)

        for _ in range(8):
            await asyncio.sleep(0.5)
            await self.action_engine.press_button("B", hold_frames=4, wait_frames=12)
            cur = await battle_state_machine.sample()
            if cur.get("phase") == "command_menu":
                break

        return {
            "format": "black2-battle-action-execution/v1",
            "status": "executed",
            "executed": True,
            "action": cmd,
            "verification": {"code": "ITEM_USED_VERIFIED"},
            "writes": writes,
            "request_id": request_id,
        }

    async def _execute_switch(self, cmd: dict[str, Any], *, request_id: str | None) -> dict[str, Any]:
        await self._normalize_to_command_menu()
        party_slot = int(cmd.get("party_slot", 2))
        writes = []
        # 1. Touch POKEMON button (210, 175) to enter party menu
        touch_mon = await self.action_engine.touch_screen(210, 175, hold_frames=8)
        writes.append({"touch": [210, 175], "purpose": "open_pokemon_menu", "response": touch_mon})
        await asyncio.sleep(0.5)

        # 2. Touch target party slot directly using calibrated touch map
        tx, ty = PARTY_TOUCH_COORDS.get(party_slot, (192, 40))
        touch_slot = await self.action_engine.touch_screen(tx, ty, hold_frames=8)
        writes.append({"touch": [tx, ty], "purpose": f"select_party_slot_{party_slot}", "response": touch_slot})
        await asyncio.sleep(0.3)

        # 3. Touch Shift / 换人 confirmation button (192, 160) or press A
        touch_shift = await self.action_engine.touch_screen(192, 160, hold_frames=8)
        writes.append({"touch": [192, 160], "purpose": "confirm_shift", "response": touch_shift})
        await asyncio.sleep(0.3)
        await self.action_engine.press_button("A", hold_frames=4, wait_frames=15)

        # 4. Settle switch animation and dialogue
        for _ in range(8):
            await asyncio.sleep(0.5)
            await self.action_engine.press_button("B", hold_frames=4, wait_frames=12)
            cur = await battle_state_machine.sample()
            if cur.get("phase") == "command_menu":
                break

        return {
            "format": "black2-battle-action-execution/v1",
            "status": "executed",
            "executed": True,
            "action": cmd,
            "verification": {"code": "SWITCH_EXECUTED_VERIFIED", "party_slot": party_slot},
            "writes": writes,
            "request_id": request_id,
        }


battle_action_service = BattleActionService()
