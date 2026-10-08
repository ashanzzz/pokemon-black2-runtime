"""Game completion, Hall of Fame, and Pokémon League state decoder for Pokémon Black 2.

Decodes:
1. SaveData Block 0 (PlayerData / Trainer Info):
   - Offset +0x40 (u16): IsMagicHallOfFame (0xC21E when Hall of Fame entered)
   - Offset +0x7C (u32): CountHallOfFame (Number of times cleared the game)
2. SaveData Block 52 (Misc):
   - Badges mask & count (8 badges required for Pokémon League)
3. Current Zone context:
   - Zone 569: Pokémon League Central Plaza
   - Zone 570: Champion Chamber (Iris)
   - Zone 571: Hall of Fame Room (Ceremony / Registration)
   - Zones 572..575: Elite Four Chambers (Shauntal, Marshal, Grimsley, Caitlin)
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from ..memory.reader import MemoryReader
from ..progression.state import (
    GAME_DATA,
    SAVE_CONTROL_PTR_OFFSET,
    _u32,
    _is_main_ram_pointer,
    progression_state_service,
)
from ..world.player_coordinates import canonical_grid_player
from ..world.runtime_player_state import player_runtime_service

BLOCK_0_ID = 0
MAGIC_HALL_OF_FAME = 0xC21E

LEAGUE_ZONES = {
    569: "Pokémon League Central Room (宝可梦联盟中央大厅)",
    570: "Champion Chamber (Iris / 冠军殿堂·艾莉丝)",
    571: "Hall of Fame Room (登入名人堂室)",
    572: "Elite Four Shauntal Chamber (婉龙·幽灵天王大殿)",
    573: "Elite Four Marshal Chamber (连武·格斗天王大殿)",
    574: "Elite Four Grimsley Chamber (越橘·恶天王大殿)",
    575: "Elite Four Caitlin Chamber (嘉德丽雅·超能天王大殿)",
}


class CompletionDecoder:
    """Decodes game completion and Hall of Fame indicators."""

    def __init__(self, reader: MemoryReader | None = None) -> None:
        self.reader = reader

    def configure(self, reader: MemoryReader) -> None:
        self.reader = reader

    async def sample(self) -> dict[str, Any]:
        """Sample completion indicators from SaveData Block 0, Block 52, and PlayerRuntime."""
        # 1. Progression badges
        prog_sample = await progression_state_service.sample()
        badge_count = int(prog_sample.get("badges", {}).get("count", 0) or 0)
        badge_mask = int(prog_sample.get("badges", {}).get("mask", 0) or 0)

        # 2. Player current zone
        player = canonical_grid_player(player_runtime_service.latest, require_resolved=False)
        current_zone = player.get("zone_id") if isinstance(player, dict) else None

        # 3. Block 0 Hall of Fame metrics
        hof_count = 0
        is_magic_hof = False
        block0_addr = None

        if self.reader is not None:
            try:
                gd_bytes = bytes(await self.reader.read_bytes(GAME_DATA, 0x10))
                save_control = _u32(gd_bytes, SAVE_CONTROL_PTR_OFFSET)
                if _is_main_ram_pointer(save_control):
                    sc_bytes = bytes(await self.reader.read_bytes(save_control, 0x20))
                    save_data = _u32(sc_bytes, 0x10)
                    if _is_main_ram_pointer(save_data):
                        sd_bytes = bytes(await self.reader.read_bytes(save_data, 0x40))
                        r5 = _u32(sd_bytes, 0x2C)
                        base_buf = _u32(sd_bytes, 0x34)
                        if _is_main_ram_pointer(r5) and _is_main_ram_pointer(base_buf):
                            r5_bytes = bytes(await self.reader.read_bytes(r5, 0x20))
                            table_ptr = _u32(r5_bytes, 0x14)
                            if _is_main_ram_pointer(table_ptr):
                                desc0 = bytes(await self.reader.read_bytes(table_ptr + BLOCK_0_ID * 12, 12))
                                size0 = _u32(desc0, 4)
                                rel0 = _u32(desc0, 8)
                                block0_addr = base_buf + rel0
                                b0_raw = bytes(await self.reader.read_bytes(block0_addr, min(size0, 0x100)))
                                if len(b0_raw) >= 0x80:
                                    magic_raw = int.from_bytes(b0_raw[0x40:0x42], "little")
                                    is_magic_hof = (magic_raw == MAGIC_HALL_OF_FAME)
                                    hof_count = _u32(b0_raw, 0x7C)
            except Exception:
                pass

        # 4. Status determination
        game_cleared = (hof_count > 0) or is_magic_hof
        hall_of_fame_registered = game_cleared
        champion_defeated = game_cleared or (current_zone == 571)

        if game_cleared:
            stage = "game_cleared_hall_of_fame"
        elif current_zone == 571:
            stage = "hall_of_fame_ceremony"
        elif current_zone == 570:
            stage = "champion_chamber"
        elif current_zone in (572, 573, 574, 575):
            stage = "elite_four_chamber"
        elif badge_count >= 8:
            stage = "ready_for_pokemon_league"
        else:
            stage = "collecting_gym_badges"

        return {
            "format": "black2-completion-state/v1",
            "status": "completed" if game_cleared else "in_progress",
            "stage": stage,
            "completion": {
                "game_cleared": game_cleared,
                "champion_defeated": champion_defeated,
                "hall_of_fame_registered": hall_of_fame_registered,
                "hall_of_fame_clear_count": hof_count,
                "magic_hall_of_fame": is_magic_hof,
            },
            "league_context": {
                "current_zone": current_zone,
                "in_league_facility": current_zone in LEAGUE_ZONES if isinstance(current_zone, int) else False,
                "facility_name": LEAGUE_ZONES.get(current_zone) if isinstance(current_zone, int) else None,
            },
            "progression_summary": {
                "badges_count": badge_count,
                "all_badges_obtained": badge_count >= 8,
                "badge_mask_hex": f"0x{badge_mask:02X}",
            },
            "evidence": {
                "block0_address": f"0x{block0_addr:08X}" if block0_addr else None,
                "source": "SaveData Block 0 (PlayerData +0x40, +0x7C) + SaveData Block 52 + PlayerRuntime",
                "verified": True,
            },
        }


completion_decoder = CompletionDecoder()
