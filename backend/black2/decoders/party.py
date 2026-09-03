"""Pokémon Gen 5 (Black 2 / White 2) Party Pokémon Decoder."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel


# Gen 5 starter and common Unova Pokémon species names
SPECIES_MAP = {
    0: "None",
    494: "比克提尼 (Victini)",
    495: "藤藤蛇 (Snivy)",
    496: "青藤蛇 (Servine)",
    497: "君主蛇 (Serperior)",
    498: "暖暖猪 (Tepig)",
    499: "炒炒猪 (Pignite)",
    500: "炎武王 (Emboar)",
    501: "水水獭 (Oshawott)",
    502: "双刃丸 (Dewott)",
    503: "大剑鬼 (Samurott)",
    504: "探探鼠 (Patrat)",
    505: "步哨鼠 (Watchog)",
    506: "小约克 (Lillipup)",
    507: "哈约克 (Herdier)",
    508: "长毛狗 (Stoutland)",
    509: "扒手猫 (Purrloin)",
    510: "酷豹 (Liepard)",
    511: "花香猴 (Pansage)",
    513: "爆香猴 (Pansear)",
    515: "冷水猴 (Panpour)",
    517: "食梦梦 (Munna)",
    519: "豆豆鸽 (Pidove)",
    520: "咕咕鸽 (Tranquill)",
    521: "高傲雉鸡 (Unfezant)",
    522: "斑斑马 (Blitzle)",
    524: "石丸子 (Roggenrola)",
    529: "螺钉地鼠 (Drilbur)",
    530: "龙头地鼠 (Excadrill)",
    531: "差不多娃娃 (Audino)",
    532: "搬运小匠 (Timburr)",
    540: "虫宝包 (Sewaddle)",
    543: "百足蜈蚣 (Venipede)",
    546: "风妖精 (Cottonee)",
    548: "百合根娃娃 (Petilil)",
    550: "勇士雄鹰 (Rufflet)",
    570: "索罗亚 (Zorua)",
    571: "索罗亚克 (Zoroark)",
    643: "莱希拉姆 (Reshiram)",
    644: "捷克罗姆 (Zekrom)",
    646: "酋雷姆 (Kyurem)"
}


class PokemonPartyMember(BaseModel):
    slot: int
    species_id: int
    species_name: str
    nickname: Optional[str] = None
    level: int = 1
    current_hp: int = 0
    max_hp: int = 0
    status_condition: str = "Healthy"
    item_id: int = 0
    experience: int = 0
    moves: List[str] = []


class PartyState(BaseModel):
    count: int = 0
    members: List[PokemonPartyMember] = []


def get_species_name(species_id: int) -> str:
    return SPECIES_MAP.get(species_id, f"宝可梦 #{species_id}")
