from types import SimpleNamespace

from backend.black2.world.encounter_regions import EncounterRegionService, classify_material


class FakeProvider:
    revision = "fake-rev"

    def __init__(self, cells):
        self.cells = cells

    @staticmethod
    def _anchor_from_sample(sample, zone_id):
        return None

    def _cells_for_layer(self, zone_id, y, anchor=None):
        return self.cells


def cell(kind, encounter=None, status="verified", requires=None, tile_class=4):
    material = {"kind": kind, "status": status}
    if encounter is not None:
        material["encounter"] = encounter
    if requires is not None:
        material["requires"] = requires
    return SimpleNamespace(material=material, tile_class=tile_class)


def test_material_classification_keeps_water_probable():
    grass = classify_material({"kind": "tall_grass", "status": "verified", "encounter": "single"})
    assert grass.encounter_eligible is True
    assert grass.method == "walk_regular"
    assert grass.evidence == "verified"

    water = classify_material({"kind": "water", "status": "probable", "requires": "surf"})
    assert water.encounter_eligible is None
    assert water.method == "surf_candidate"
    assert water.evidence == "probable"


def test_regions_use_four_neighbour_components_and_exact_tile_sets():
    cells = {
        (0, 0): cell("tall_grass", "single"),
        (1, 0): cell("tall_grass", "single"),
        (0, 1): cell("tall_grass", "single"),
        (1, 1): cell("tall_grass", "single"),
        # Diagonal-only contact must not merge.
        (2, 2): cell("tall_grass", "single"),
        (8, 0): cell("water", status="probable", requires="surf", tile_class=0x3D),
        (9, 0): cell("water", status="probable", requires="surf", tile_class=0x3D),
    }
    service = EncounterRegionService(FakeProvider(cells))
    payload = service.zone_regions(439, 1)
    assert payload["region_count"] == 3

    grass = [r for r in payload["regions"] if r["encounter_method"] == "walk_regular"]
    assert sorted(r["tile_count"] for r in grass) == [1, 4]
    square = next(r for r in grass if r["tile_count"] == 4)
    assert square["patrol"]["recommended_strategy"] == "loop"
    assert len(square["patrol"]["route"]) == 4
    assert len(square["outline_segments"]) == 8
    assert {(t["x"], t["z"]) for t in square["tiles"]} == {(0, 0), (1, 0), (0, 1), (1, 1)}

    water = next(r for r in payload["regions"] if r["encounter_method"] == "surf_candidate")
    assert water["evidence"] == "probable"
    assert water["encounter_eligible"] is None
    assert water["requirements"] == ["surf"]
    assert water["patrol"]["recommended_strategy"] == "ping_pong"


def test_single_tile_region_does_not_claim_turning_triggers_encounters():
    service = EncounterRegionService(FakeProvider({(4, 5): cell("dark_grass", "double", tile_class=6)}))
    region = service.zone_regions(439, 1)["regions"][0]
    assert region["patrol"]["possible"] is False
    assert "turning in place" in region["patrol"]["reason"]
