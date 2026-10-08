from backend.black2.world.location_catalog import RomLocationCatalog
from backend.black2.world.static_navigation import RomStaticNavigationGraph


def test_rom_location_catalog_resolves_parent_and_environment():
    provider = RomStaticNavigationGraph()
    catalog = RomLocationCatalog(provider.rom)
    label = catalog.zone_label(335)
    assert "引导之间" in label.name_zh
    assert label.parent_name_zh == "吹寄洞穴"
    assert label.environment == "cave"
    assert "洞穴内" in label.display_name


def test_rom_location_catalog_distinguishes_outdoor_and_center():
    provider = RomStaticNavigationGraph()
    catalog = RomLocationCatalog(provider.rom)
    assert catalog.zone_label(439).environment == "outdoor"
    assert catalog.zone_label(443).environment == "pokemon_center"
