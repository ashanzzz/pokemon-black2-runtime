from __future__ import annotations

import threading
from types import SimpleNamespace

from backend.black2.world.navigation_planning import NavigationPlanService, NavigationPlanningError
from backend.black2.world.observed_navigation import NavNode, ObservedNavigationGraph
from backend.black2.world.static_navigation import RomStaticNavigationGraph, StaticNavigationCell


class FakeMatrix:
    matrix_id = 0
    has_zones = True
    width = 2
    height = 1
    zone_ids = (10, 11)
    chunk_ids = (100, 101)

    def cell(self, x: int, y: int):
        if not (0 <= x < 2 and y == 0):
            raise IndexError((x, y))
        return {"index": x, "x": x, "y": 0, "chunk_id": 100 + x, "zone_id": self.zone_ids[x]}

    def cells(self):
        for x in range(2):
            yield self.cell(x, 0)


class FakeRom:
    def static_identity(self):
        return {"fake": True}

    def matrix(self, matrix_id: int):
        assert matrix_id == 0
        return FakeMatrix()

    def zone(self, zone_id: int):
        if zone_id not in (10, 11):
            raise IndexError(zone_id)
        return SimpleNamespace(matrix_id=0, area_id=zone_id, enable_running=True, enable_cycling=True)

    def area(self, area_id: int):
        return SimpleNamespace(is_exterior=True)


class FakeGlobalProvider(RomStaticNavigationGraph):
    def __init__(self):
        self.rom = FakeRom()
        self.revision = "fake-global-v13"
        self._lock = threading.RLock()
        self._zone_surfaces_cache = {}
        self._layer_cache = {}
        self._fake_cells = {
            10: self._line(10, range(28, 32)),
            11: self._line(11, range(32, 36)),
        }

    @staticmethod
    def _line(zone_id: int, xs):
        result = {}
        for x in xs:
            node = NavNode(zone_id, x, 0, 5)
            result[(x, 5)] = StaticNavigationCell(
                node=node,
                layer_index=0,
                tile_class=0,
                flags=0,
                static_blocked=False,
                material={"kind": "ground", "status": "verified"},
            )
        return result

    def _cells_for_layer(self, zone_id: int, y: int, *, anchor=None):
        return self._fake_cells.get(int(zone_id), {}) if int(y) == 0 else {}



def player_sample(x=30, zone=10):
    return {
        "status": "resolved",
        "confidence": "verified",
        "frame": 100,
        "zone_id": zone,
        "position": {
            "grid": {"x": x, "y": 0, "z": 5},
            "world": {"x": x * 16 + 8, "y": 0.0, "z": 5 * 16 + 8},
        },
        "grid": {"x": x, "y": 0, "z": 5},
        "world": {"x": x * 16 + 8, "y": 0.0, "z": 5 * 16 + 8},
        "locomotion": {"transport_mode": "OnFoot"},
    }


def test_zone_less_coordinate_resolves_from_matrix_ownership():
    provider = FakeGlobalProvider()
    assert provider.resolve_zone_for_global(0, 31, 5) == 10
    assert provider.resolve_zone_for_global(0, 32, 5) == 11
    assert provider.resolve_zone_for_global(0, 64, 5) is None


def test_global_astar_crosses_zone_boundary_without_connector():
    provider = FakeGlobalProvider()
    result = provider.find_global_path(
        NavNode(10, 30, 0, 5), matrix_id=0, x=34, y=0, z=5, player_sample=player_sample()
    )
    assert result["reachable"] is True
    assert result["steps"] == 4
    assert [point["x"] for point in result["path"]] == [30, 31, 32, 33, 34]
    assert [point["zone_id"] for point in result["path"]] == [10, 10, 11, 11, 11]
    assert result["zone_transitions"] == [
        {"from_zone_id": 10, "to_zone_id": 11, "at": {"x": 32, "y": 0, "z": 5}}
    ]
    assert result["decoded_zone_ids"] == [10, 11]


def test_planner_accepts_global_destination_without_zone_id():
    provider = FakeGlobalProvider()
    planner = NavigationPlanService(ObservedNavigationGraph(), lambda: player_sample(), static_provider=provider)
    plan = planner.create_plan({
        "type": "global_grid",
        "space": "gen5-matrix-grid-v1",
        "x": 34,
        "y": 0,
        "z": 5,
    })
    assert plan["status"] == "ready"
    assert plan["resolved_goal"]["zone_id"] == 11
    assert plan["resolved_goal"]["zone_resolved_from_global"] is True
    assert plan["resolved_goal"]["global"]["matrix_id"] == 0
    assert plan["segments"][0]["kind"] == "matrix_global"
    assert plan["segments"][0]["matrix_id"] == 0
    assert plan["cost"]["zone_transitions"] == 1
    assert plan["movement"]["selected"] == "run"


def test_same_matrix_zone_labels_are_metadata_for_executor_position_checks():
    provider = FakeGlobalProvider()
    planner = NavigationPlanService(ObservedNavigationGraph(), lambda: player_sample(), static_provider=provider)
    assert planner.same_spatial_node(NavNode(10, 32, 0, 5), NavNode(11, 32, 0, 5)) is True
    assert planner.same_spatial_node(NavNode(10, 31, 0, 5), NavNode(11, 32, 0, 5)) is False


def test_cross_matrix_global_destination_still_requires_verified_connector():
    provider = FakeGlobalProvider()
    planner = NavigationPlanService(ObservedNavigationGraph(), lambda: player_sample(), static_provider=provider)
    try:
        planner.create_plan({
            "type": "global_grid", "space": "gen5-matrix-grid-v1",
            "matrix_id": 1, "x": 5, "y": 0, "z": 5,
        })
    except NavigationPlanningError as exc:
        assert exc.code == "NAV_MATRIX_TRANSITION_UNVERIFIED"
    else:
        raise AssertionError("cross-Matrix global destination must require a verified connector")
