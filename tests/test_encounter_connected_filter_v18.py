from types import SimpleNamespace

from backend.black2.world.encounter_regions import EncounterRegionService


class Matrix:
    matrix_id = 1

    def cells(self):
        return [
            {"x": 0, "y": 0, "zone_id": 439, "chunk_id": 0},
            {"x": 1, "y": 0, "zone_id": 0xFFFFFFFF, "chunk_id": 0xFFFF},
        ]


class Rom:
    zone_count = 615

    def zone(self, _zone_id):
        return SimpleNamespace(matrix_id=1, area_id=1)

    def matrix(self, _matrix_id):
        return Matrix()

    def area(self, _area_id):
        return SimpleNamespace(is_exterior=True)


def test_connected_regions_skips_empty_matrix_owners():
    provider = SimpleNamespace(rom=Rom())
    service = EncounterRegionService(provider)  # type: ignore[arg-type]
    payload = service.connected_zone_ids(439)
    assert payload["zone_ids"] == [439]
    assert payload["adjacency"] == []

