"""Unified Gen 5 battle state machine and decision pipeline for Pokémon Black 2.

Combines live BattleRuntime, BattleUiCursor, BattleIdentity, and BattlePlanner
into a single deterministic battle decision engine.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from ..decoders.battle_runtime import BattleRuntimeDecoder
from ..decoders.battle_ui_cursor import BattleUiCursorDecoder
from ..decoders.battle_identity import BattleIdentityDecoder
from ..decoders.party_runtime import PlayerPartyDecoder
from .battle_planner import plan_battle_decision


class BattleStateMachine:
    """Manages the full lifecycle of a Gen 5 battle from encounter to resolution."""

    def __init__(
        self,
        runtime_decoder: BattleRuntimeDecoder | None = None,
        ui_cursor_decoder: BattleUiCursorDecoder | None = None,
        identity_decoder: BattleIdentityDecoder | None = None,
    ) -> None:
        self.runtime_decoder = runtime_decoder or BattleRuntimeDecoder()
        self.ui_cursor_decoder = ui_cursor_decoder or BattleUiCursorDecoder()
        self.identity_decoder = identity_decoder or BattleIdentityDecoder()
        self.party_decoder = PlayerPartyDecoder()

    def configure(self, reader: Any) -> None:
        self.runtime_decoder.configure(reader)
        self.ui_cursor_decoder.configure(reader)
        self.identity_decoder.configure(reader)
        self.party_decoder.configure(reader)

    async def sample(
        self,
        *,
        capture_eval: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sample all battle subsystems and construct an authoritative decision snapshot."""
        # 1. Runtime evidence
        try:
            evidence = await self.runtime_decoder.sample()
        except Exception:
            evidence = {"active": False, "active_status": "unresolved"}

        active = evidence.get("active") is True
        if not active:
            return {
                "format": "black2-battle-state-machine/v1",
                "status": "not_in_battle",
                "active": False,
                "phase": "not_in_battle",
                "cursor": None,
                "player": None,
                "opponent": None,
                "decision": None,
                "can_act": False,
            }

        # 2. UI cursor & phase
        try:
            ui_sample = await self.ui_cursor_decoder.sample()
        except Exception:
            ui_sample = {"status": "unresolved"}

        phase_raw = (ui_sample.get("phase") or {}).get("value", "unresolved")
        cursor_data = ui_sample.get("cursor")

        # 3. Combatant identity
        party_data = None
        try:
            party_data = await self.party_decoder.sample()
        except Exception:
            pass
        try:
            identity = await self.identity_decoder.sample(presence=evidence, player_party=party_data)
        except Exception:
            identity = {}

        player_data = identity.get("player") or {}
        opp_data = identity.get("opponent") or {}
        battle_kind_data = identity.get("battle_kind") or {}
        battle_kind = battle_kind_data.get("value") or ("trainer" if "trainer" in str(identity).lower() else "wild")

        # 4. Compute AI decision plan
        decision = plan_battle_decision(
            player_mon=player_data.get("active") or player_data,
            opponent_mon=opp_data.get("active") or opp_data,
            battle_kind=battle_kind,
            capture_eval=capture_eval,
        )

        # 5. Determine whether an action can be safely executed right now
        can_act = phase_raw in ("command_menu", "move_menu")

        return {
            "format": "black2-battle-state-machine/v1",
            "status": "active_battle",
            "active": True,
            "battle_kind": battle_kind,
            "phase": phase_raw,
            "cursor": cursor_data,
            "player": player_data,
            "opponent": opp_data,
            "decision": decision,
            "recommended_action": decision.get("recommended_action"),
            "can_act": can_act,
            "evidence": {
                "runtime_frame": evidence.get("frame"),
                "source": "BattleRuntime + BattleUiCursor + BattleIdentity + BattlePlanner",
            },
        }


battle_state_machine = BattleStateMachine()
