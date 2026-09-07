from types import SimpleNamespace

from backend.black2.world.world3d_scene import World3DSceneService


class _Matrix:
    matrix_id = 10
    has_zones = True
    width = 5
    height = 2
    cell_count = 10
    zone_ids = (1, 1, 2, 3, 9, 1, 2, 2, 3, 9)

    def cells(self):
        zones = [1, 1, 2, 3, 9, 1, 2, 2, 3, 9]
        # Zone 9 is separated from Zone 3 by an empty column at x=4? Keep its
        # chunks absent so it is not spatially selected despite being named.
        for index, zone_id in enumerate(zones):
            x, y = index % self.width, index // self.width
            chunk = 0xFFFFFFFF if x == 4 else 100 + index
            yield {"x": x, "y": y, "chunk_id": chunk, "zone_id": zone_id}


class _Rom:
    def matrix(self, matrix_id):
        assert matrix_id == 10
        return _Matrix()

    def zone(self, zone_id):
        return SimpleNamespace(zone_id=zone_id, area_id=zone_id, matrix_id=10)

    def area(self, _area_id):
        return SimpleNamespace(is_exterior=True)


class _Original:
    rom = _Rom()


def test_connected_cluster_stitches_only_same_matrix_cardinal_component():
    service = World3DSceneService(original=_Original(), truth=object(), exported=object())
    cluster = service.connected_zone_cluster(1, matrix_id=10)
    assert cluster["alignment"] == "shared_matrix_exact"
    assert cluster["zone_ids"] == [1, 2, 3]
    assert cluster["zone_count"] == 3
    assert {tuple(sorted((edge["zone_a"], edge["zone_b"]))) for edge in cluster["adjacency"]} == {(1, 2), (2, 3)}
    assert 9 not in cluster["zone_ids"]
    assert "cross_matrix" in cluster["cross_matrix_policy"]
