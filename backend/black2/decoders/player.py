"""Pokémon Gen 5 (Black 2 / White 2) Player Trainer Profile Decoder."""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class BadgeStatus(BaseModel):
    name: str
    obtained: bool = False


class PlayerTrainerState(BaseModel):
    name: str = "训练家"
    gender: str = "Male"  # "Male" or "Female"
    trainer_id: int = 0
    secret_id: int = 0
    money: int = 0
    badges_count: int = 0
    badges: List[BadgeStatus] = [
        BadgeStatus(name="基础徽章 (Basic Badge)", obtained=False),
        BadgeStatus(name="毒性徽章 (Toxic Badge)", obtained=False),
        BadgeStatus(name="甲虫徽章 (Insect Badge)", obtained=False),
        BadgeStatus(name="伏特徽章 (Bolt Badge)", obtained=False),
        BadgeStatus(name="震动徽章 (Quake Badge)", obtained=False),
        BadgeStatus(name="喷射徽章 (Jet Badge)", obtained=False),
        BadgeStatus(name="传说徽章 (Legend Badge)", obtained=False),
        BadgeStatus(name="海浪徽章 (Wave Badge)", obtained=False),
    ]
    play_time_hours: int = 0
    play_time_minutes: int = 0
    play_time_seconds: int = 0
    pokedex_seen: int = 0
    pokedex_caught: int = 0
    pc_boxes_count: int = 24
