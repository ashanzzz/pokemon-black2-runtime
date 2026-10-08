from pathlib import Path
import pytest
from backend.black2.world.gym_catalog import default_gym_catalog

ROM = Path(r"D:\game\desmume-0.9.13-win64\口袋妖怪黑2.nds")


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_gym_catalog_all_gyms_count_and_indices():
    cat = default_gym_catalog()
    all_gyms = cat.get_all_gyms()
    assert len(all_gyms) == 8
    indices = [g["gym_index"] for g in all_gyms]
    assert indices == [1, 2, 3, 4, 5, 6, 7, 8]


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_gym_catalog_aspertia_gym_details():
    cat = default_gym_catalog()
    gym1 = cat.get_gym_by_zone(489)
    assert gym1 is not None
    assert gym1["gym_name_zh"] == "桧扇道馆"
    assert gym1["leader_name_zh"] == "切莲"
    assert gym1["badge_name_zh"] == "基础徽章"
    assert gym1["reward_tm"]["tm_id"] == 83

    # Leader party
    assert gym1["leader"] is not None
    assert gym1["leader"]["trainer_id"] == 156
    assert [p["species_id"] for p in gym1["leader"]["party"]] == [504, 506]

    # Subordinate trainers
    sub_ids = [t["trainer_id"] for t in gym1["subordinate_trainers"]]
    assert 171 in sub_ids # Shinya
    assert 172 in sub_ids # Mariko


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_gym_catalog_nimbasa_gym_details():
    cat = default_gym_catalog()
    gym4 = cat.get_gym_by_zone(63)
    assert gym4 is not None
    assert gym4["gym_name_zh"] == "雷文道馆"
    assert gym4["leader_name_zh"] == "小菊儿"
    assert gym4["badge_name_zh"] == "伏特徽章"
    assert gym4["reward_tm"]["tm_id"] == 72
