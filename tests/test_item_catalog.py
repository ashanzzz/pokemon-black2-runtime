from pathlib import Path
import pytest
from backend.black2.world.item_catalog import default_item_catalog, RomItemCatalog

ROM = Path(r"D:\game\desmume-0.9.13-win64\口袋妖怪黑2.nds")


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_item_catalog_resolves_standard_items():
    cat = default_item_catalog()

    poke_ball = cat.get_item(4)
    assert poke_ball is not None
    assert poke_ball["name_zh"] == "精灵球"
    assert poke_ball["price"] == 200
    assert poke_ball["pocket_id"] == "items"
    assert "胶囊" in poke_ball["description_zh"]

    potion = cat.get_item(17)
    assert potion is not None
    assert potion["name_zh"] == "药水"
    assert potion["price"] == 300
    assert potion["pocket_id"] == "medicine"
    assert "20HP" in potion["description_zh"]

    tm57 = cat.get_item(384)
    assert tm57 is not None
    assert tm57["name_zh"] == "技能学习器57"
    assert tm57["price"] == 10000
    assert tm57["pocket_id"] == "tm_hm"


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_item_catalog_resolves_field_item_balls():
    cat = default_item_catalog()

    # Index 81 (script 7081) -> TM57 (item 384)
    res_tm = cat.resolve_field_item(7081)
    assert res_tm["status"] == "resolved"
    assert res_tm["item_id"] == 384
    assert res_tm["name_zh"] == "技能学习器57"
    assert res_tm["count"] == 1

    # Index 82 (script 7082) -> PP Up (item 38)
    res_pp = cat.resolve_field_item(7082)
    assert res_pp["status"] == "resolved"
    assert res_pp["item_id"] == 38
    assert res_pp["name_zh"] == "PP小补剂"


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_item_catalog_resolves_hidden_items():
    cat = default_item_catalog()

    # Index 154 (script 8154) -> Max Ether (item 39)
    res_hidden = cat.resolve_hidden_item(8154)
    assert res_hidden["status"] == "resolved"
    assert res_hidden["item_id"] == 39
    assert res_hidden["name_zh"] == "PP单补剂"
