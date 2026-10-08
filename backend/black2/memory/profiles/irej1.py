"""Verified memory profile for Pokémon Black 2 Japanese / Chinese (IREJ rev.1)."""
from __future__ import annotations

from .base import MemoryProfile, MemoryRange


IREJ1_PROFILE = MemoryProfile(
    rom_code="IREJ",
    rom_title="Pokémon Black 2 (JPN/CHN)",
    revision=1,
    known_pointers={
        # Primary GameData anchor in ARM9 RAM
        "game_data": 0x0223B330,
        # Field status busy flag offset inside FieldStatus struct
        "field_status_busy_flag_offset": 0x14,
        # ScriptWork and message buffer base candidates
        "script_and_message_state": 0x02247500,
        "msg_buffer": 0x022490A0,
        # Visible Text Printer / TCBL allocation candidates
        "tcbl_base": 0x02332C00,
        "bmpwin_base": 0x02323280,
    },
    ranges={
        "game_data_chain": MemoryRange(
            name="game_data_chain",
            base_address=0x0223B330,
            size=512,
            description="Root GameData pointer chain for party and field status",
        ),
        "msg_buffer": MemoryRange(
            name="msg_buffer",
            base_address=0x022490A0,
            size=512,
            description="Active message buffer holding decoded text strings",
        ),
        "field_actor_window": MemoryRange(
            name="field_actor_window",
            base_address=0x0223D800,
            size=0x1000,
            description="FieldActor table holding player and NPC state",
        ),
        "battle_heap": MemoryRange(
            name="battle_heap",
            base_address=0x02329000,
            size=0x4000,
            description="Active battle scene dynamic memory structures",
        ),
    },
)
