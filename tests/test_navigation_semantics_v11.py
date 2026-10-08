from types import SimpleNamespace
from dataclasses import replace
from collections import OrderedDict
import threading

import pytest

from backend.black2.api import navigation_routes
from backend.black2.world.navigation_planning import NavigationPlanService, NavigationPlanningError
from backend.black2.world.observed_navigation import NavNode, ObservedNavigationGraph
from backend.black2.world.static_navigation import RomStaticNavigationGraph, StaticNavigationCell


def _grid(zone=441, x=7, y=0, z=5):
    return {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": zone, "x": x, "y": y, "z": z}


def test_coordinate_move_never_auto_upgrades_to_npc_interaction():
    destination = _grid()
    occupied = [{"zone_id": 441, "grid": {"x": 7, "y": 0, "z": 5}, "actor_id": "npc-1"}]
    resolved, interaction = navigation_routes._prepare_navigation_request(
        destination,
        provider=object(),
        player_sample=None,
        occupancy=occupied,
        interaction=None,
        navigation_intent="walk_to_tile",
    )
    assert resolved == destination
    assert interaction is None


def test_interact_requires_explicit_npc_semantics():
    with pytest.raises(NavigationPlanningError) as error:
        navigation_routes._prepare_navigation_request(
            _grid(), provider=None, player_sample=None, occupancy=[], interaction=None,
            navigation_intent="interact",
        )
    assert error.value.code == "NAV_INTERACTION_TARGET_REQUIRED"


class _MovementRom:
    def zone(self, _zone_id):
        return SimpleNamespace(area_id=1, enable_running=True, enable_cycling=True)

    def area(self, _area_id):
        return SimpleNamespace(is_exterior=True)


class _MovementProvider:
    rom = _MovementRom()

    def movement_rules(self, *_args, **_kwargs):
        return {"bike_blockers": []}


def test_auto_movement_picks_fastest_verified_active_mode():
    planner = NavigationPlanService(ObservedNavigationGraph(), lambda: None, static_provider=_MovementProvider())
    on_foot = {"locomotion": {"transport_mode": "OnFoot"}}
    cycling = {"locomotion": {"transport_mode": "Cycling"}}
    surfing = {"locomotion": {"transport_mode": "Surf"}}
    assert planner.movement_capabilities(441, requested="auto", player=on_foot)["selected"] == "run"
    assert planner.movement_capabilities(441, requested="auto", player=cycling)["selected"] == "bike"
    assert planner.movement_capabilities(441, requested="auto", player=surfing)["selected"] == "surf"


def test_static_edge_revalidation_preserves_selected_transport_mode():
    class ModeAwareProvider:
        def __init__(self):
            self.modes = []

        def has_candidate_edge(self, start, goal, *, player_sample=None,
                                occupied=(), movement_mode="walk",
                                constraint_evaluator=None):
            self.modes.append(movement_mode)
            return True

    provider = ModeAwareProvider()
    planner = NavigationPlanService(
        ObservedNavigationGraph(), lambda: {"locomotion": {"transport_mode": "Surf"}},
        static_provider=provider,
    )
    assert planner.has_static_edge(
        NavNode(441, 0, 0, 0), NavNode(441, 1, 0, 0), movement_mode="surf",
    )
    assert provider.modes == ["surf"]


class _OpenGrid(RomStaticNavigationGraph):
    def __init__(self):
        self.revision = "test-grid"
        self._cells = {}
        for z in range(3):
            for x in range(3):
                node = NavNode(441, x, 0, z)
                self._cells[(x, z)] = StaticNavigationCell(
                    node=node, layer_index=0, tile_class=0, flags=0, static_blocked=False,
                )

    def _anchor_from_sample(self, *_args, **_kwargs):
        return None

    def _cells_for_layer(self, *_args, **_kwargs):
        return self._cells


def test_static_astar_prefers_fewer_turns_without_adding_steps():
    graph = _OpenGrid()
    result = graph.find_path(NavNode(441, 0, 0, 0), NavNode(441, 2, 0, 2))
    assert result["reachable"] is True
    assert result["steps"] == 4
    assert result["turns"] == 1
    assert result["optimization"] == "shortest_steps_then_fewest_turns"


class _TransportGrid(RomStaticNavigationGraph):
    """Small deterministic map used to assert transport-aware A* rules."""

    def __init__(self):
        self.revision = "test-transport-rules"
        self._cells = {}
        # Two routes from (0,0) to (2,0): the direct middle tile is water and
        # the lower detour is ordinary ground.
        for x, z in ((0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1), (0, 2), (1, 2), (2, 2)):
            material = {"kind": "ground", "status": "verified"}
            if (x, z) == (1, 0):
                material = {"kind": "water", "status": "probable", "requires": "surf"}
            if (x, z) == (1, 1):
                material = {"kind": "very_tall_grass", "status": "verified", "blocks_cycling": True}
            self._cells[(x, z)] = StaticNavigationCell(
                node=NavNode(441, x, 0, z), layer_index=0, tile_class=0,
                flags=0, static_blocked=False, material=material,
            )

    def _anchor_from_sample(self, *_args, **_kwargs):
        return None

    def _cells_for_layer(self, *_args, **_kwargs):
        return self._cells


def test_static_astar_excludes_water_for_walk_but_allows_surf():
    graph = _TransportGrid()
    start, goal = NavNode(441, 0, 0, 0), NavNode(441, 2, 0, 0)
    walking = graph.find_path(start, goal, movement_mode="walk")
    assert walking["reachable"] is True
    assert (1, 0) not in {(p["x"], p["z"]) for p in walking["path"]}

    graph._cells[(0, 0)] = replace(graph._cells[(0, 0)], material={"kind": "water", "requires": "surf"})
    graph._cells[(2, 0)] = replace(graph._cells[(2, 0)], material={"kind": "water", "requires": "surf"})
    surfing = graph.find_path(start, goal, movement_mode="surf")
    assert surfing["reachable"] is True
    assert (1, 0) in {(p["x"], p["z"]) for p in surfing["path"]}
    land_to_water = graph.find_path(NavNode(441, 0, 0, 1), goal, movement_mode="surf")
    assert land_to_water["reachable"] is False
    assert land_to_water["movement_blocker"]["reason"] == "surf_requires_water"


def test_static_astar_excludes_cycling_blockers_for_bike():
    graph = _TransportGrid()
    result = graph.find_path(
        NavNode(441, 0, 0, 0), NavNode(441, 2, 0, 0), movement_mode="bike",
    )
    assert result["reachable"] is True
    assert (1, 1) not in {(p["x"], p["z"]) for p in result["path"]}


def test_static_astar_rejects_explicit_unknown_rom_material():
    graph = _TransportGrid()
    graph._cells[(0, 0)] = replace(
        graph._cells[(0, 0)],
        material={"kind": "unknown", "status": "unverified"},
    )
    result = graph.find_path(
        NavNode(441, 0, 0, 0), NavNode(441, 2, 0, 0), movement_mode="walk",
    )
    assert result["reachable"] is False
    assert result["movement_blocker"]["reason"] == "unknown_static_material"


class _BoundedCacheGrid(RomStaticNavigationGraph):
    """Use inherited cache and A* with deterministic decoded terrain records."""

    def __init__(self):
        self._lock = threading.RLock()
        self._zone_surfaces_cache = OrderedDict()
        self._layer_cache = OrderedDict()
        self._zone_surface_cache_cap = 2
        self._layer_cache_cap = 2
        self.revision = "test-bounded-cache"
        self.decode_count = {}

    def _decode_zone_surfaces(self, zone_id):
        self.decode_count[zone_id] = self.decode_count.get(zone_id, 0) + 1
        return tuple({
            "zone_id": zone_id,
            "matrix_cell": {"x": 0, "z": 0},
            "chunk_id": zone_id,
            "x": x, "z": 0, "local_x": x, "local_z": 0,
            "layer_index": 0,
            "surface": {
                "collision": {"static_blocked": False, "blocked_directions": [], "ledge_direction": None},
                "height": {"chunk_relative_world_y": 0},
                "raw": {"tile_class": 0, "flags": 0},
                "material": {},
            },
        } for x in range(3))


def test_static_navigation_lru_caps_decoded_terrain_without_changing_paths():
    graph = _BoundedCacheGrid()
    first = graph.find_path(NavNode(1, 0, 0, 0), NavNode(1, 2, 0, 0))
    assert first["reachable"] is True

    # Different Zone/layer requests force both small test caches past capacity.
    graph._cells_for_layer(2, 0)
    graph._cells_for_layer(3, 0)
    status = graph.status()["cache"]
    assert status == {
        "zone_surface_entries": 2, "zone_surface_cap": 2,
        "zone_surface_lookup_entries": 0,
        "layer_entries": 2, "layer_cap": 2,
    }
    assert 1 not in graph._zone_surfaces_cache

    second = graph.find_path(NavNode(1, 0, 0, 0), NavNode(1, 2, 0, 0))
    assert second["reachable"] is True
    assert second["path"] == first["path"]
    assert graph.decode_count[1] == 2


class _BoundedGlobalCacheGrid(_BoundedCacheGrid):
    class Matrix:
        matrix_id = 0
        has_zones = True
        width = 2
        height = 1
        zone_ids = (1, 2)

        @staticmethod
        def cell(x, y):
            if y != 0 or x not in (0, 1):
                raise IndexError((x, y))
            return {"chunk_id": x + 1, "zone_id": x + 1}

    class Rom:
        @staticmethod
        def static_identity():
            return {"cache_test": True}

        @staticmethod
        def zone(zone_id):
            if zone_id not in (1, 2, 3):
                raise IndexError(zone_id)
            return SimpleNamespace(matrix_id=0, area_id=0, enable_running=True, enable_cycling=True)

        @staticmethod
        def matrix(matrix_id):
            if matrix_id != 0:
                raise IndexError(matrix_id)
            return _BoundedGlobalCacheGrid.Matrix()

    def __init__(self):
        super().__init__()
        self.rom = self.Rom()
        self._zone_surface_cache_cap = 1
        self._layer_cache_cap = 1

    def _decode_zone_surfaces(self, zone_id):
        self.decode_count[zone_id] = self.decode_count.get(zone_id, 0) + 1
        xs = range(30, 32) if zone_id == 1 else range(32, 35) if zone_id == 2 else range(40, 43)
        return tuple({
            "zone_id": zone_id,
            "matrix_cell": {"x": 0 if x < 32 else 1, "z": 0},
            "chunk_id": 1 if x < 32 else 2,
            "x": x, "z": 5, "local_x": x % 32, "local_z": 5,
            "layer_index": 0,
            "surface": {
                "collision": {"static_blocked": False, "blocked_directions": [], "ledge_direction": None},
                "height": {"chunk_relative_world_y": 0},
                "raw": {"tile_class": 0, "flags": 0},
                "material": {},
            },
        } for x in xs)


def test_static_navigation_lru_does_not_change_matrix_global_path():
    graph = _BoundedGlobalCacheGrid()
    first = graph.find_global_path(NavNode(1, 30, 0, 5), matrix_id=0, x=34, y=0, z=5)
    assert first["reachable"] is True
    graph._cells_for_layer(3, 0)  # evict both route Zones from cap-one caches
    assert len(graph._zone_surfaces_cache) == len(graph._layer_cache) == 1

    second = graph.find_global_path(NavNode(1, 30, 0, 5), matrix_id=0, x=34, y=0, z=5)
    assert second["reachable"] is True
    assert second["path"] == first["path"]
    assert graph.decode_count[1] >= 2


class _DisconnectedClickGrid(RomStaticNavigationGraph):
    def __init__(self):
        self.revision = "test-disconnected-click"
        self._cells = {
            (0, 0): StaticNavigationCell(
                NavNode(441, 0, 0, 0), 0, 0, 0, False
            ),
            (1, 0): StaticNavigationCell(
                NavNode(441, 1, 0, 0), 0, 0, 0, False
            ),
            (2, 0): StaticNavigationCell(
                NavNode(441, 2, 0, 0), 0, 0, 0, False,
                blocked_directions=("right",),
            ),
            (3, 0): StaticNavigationCell(
                NavNode(441, 3, 0, 0), 0, 0, 0, False,
                blocked_directions=("left",),
            ),
        }

    def _anchor_from_sample(self, *_args, **_kwargs):
        return None

    def _cells_for_layer(self, *_args, **_kwargs):
        return self._cells


def test_snap_rejects_exact_static_tile_when_disconnected_from_live_player():
    graph = _DisconnectedClickGrid()
    player = {
        "zone_id": 441,
        "grid": {"x": 0, "y": 0, "z": 0},
    }

    result = graph.snap(
        441,
        3,
        0,
        0,
        player_sample=player,
        max_radius=4,
    )

    assert result["ok"] is True
    assert result["snapped"] is True
    assert result["target"] == {
        "zone_id": 441,
        "x": 2,
        "y": 0,
        "z": 0,
    }
