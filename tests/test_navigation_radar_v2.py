from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.api import navigation_routes
from backend.black2.api.navigation_routes import _aggregate_fuzzy, _classify_surface_meta
from backend.black2.world.navigation_planning import NavigationPlanService
from backend.black2.world.observed_navigation import ObservedNavigationGraph, NavNode
from backend.black2.world.static_navigation import RomStaticNavigationGraph, StaticNavigationCell


class _FakeRom:
    zone_count = 1

    def zone(self, zone_id):
        if int(zone_id) != 445:
            raise IndexError(zone_id)
        return SimpleNamespace(matrix_id=255, area_id=1)


class RadarProvider:
    """Small ROM-backed provider double for the public radar contract."""

    def __init__(self):
        self.rom = _FakeRom()
        self.revision = "radar-v2-test"
        self.events = {
            (2, 0): [{"kind": "warp", "symbol": "D", "id": 1}],
            (4, 4): [{"kind": "npc", "symbol": "N", "id": 2}],
            (3, 1): [{"kind": "signpost", "symbol": "S", "arg3_raw": 6, "position": {"x": 3, "z": 1}}],
        }

    def _meta(self, x, z):
        if (x, z) == (1, 1):
            return 0x72, 1, {"kind": "ledge", "ledge_direction": "right"}, True, [], "right"
        if (x, z) == (3, 3):
            return 0x51, 1, {"kind": "directional_barrier", "blocked_directions": ["right"]}, True, ["right"], None
        if (x, z) == (0, 0):
            return 1, 1, {"kind": "obstacle"}, True, [], None
        return 31, 128, {"kind": "ground", "status": "verified"}, False, [], None

    def surface_at(self, zone_id, x, z, y, **kwargs):
        if int(zone_id) != 445 or not (0 <= int(x) < 5 and 0 <= int(z) < 5):
            return {"walkable": False, "movement_allowed": False, "cell": None, "surfaces": []}
        tile_class, flags, material, blocked, blocked_dirs, ledge = self._meta(int(x), int(z))
        cell = {
            "x": int(x), "z": int(z), "y": int(y),
            "tile_class": tile_class, "flags": flags,
            "static_blocked": blocked,
            "blocked_directions": blocked_dirs,
            "ledge_direction": ledge,
            "material": material,
        }
        return {
            "walkable": True,
            "movement_allowed": True,
            "cell": cell,
            "surfaces": [{
                "tile_class": tile_class, "flags": flags,
                "static_blocked": blocked, "material": material,
            }],
        }

    def event_overlay_at(self, zone_id, x, z):
        return list(self.events.get((int(x), int(z)), ()))

    def zone_bounds(self, zone_id):
        return {"zone_id": int(zone_id), "bounds": {"min_x": 0, "max_x": 4, "min_z": 0, "max_z": 4, "width": 5, "height": 5}, "tile_count": 25}

    def matrix_bounds(self, matrix_id):
        return {"matrix_id": int(matrix_id), "bounds": {"min_x": 0, "max_x": 4, "min_z": 0, "max_z": 4, "width": 5, "height": 5}, "zone_ids": [445]}

    def matrix_catalog(self):
        return [{"matrix_id": 255, "width_chunks": 1, "height_chunks": 1, "zone_ids": [445]}]

    def resolve_zone_for_global(self, matrix_id, x, z, preferred_zone=None):
        return 445 if int(matrix_id) == 255 and 0 <= int(x) < 5 and 0 <= int(z) < 5 else None


def _player():
    return {
        "status": "resolved", "confidence": "verified", "zone_id": 445,
        "position": {"grid": {"x": 2, "y": 2, "z": 2}},
        "orientation": {"facing": "West", "facing_zh": "西"},
    }


def _client(provider):
    navigation_routes.player_runtime_service.latest = _player()
    navigation_routes.configure_navigation_routes(
        NavigationPlanService(ObservedNavigationGraph(), lambda: navigation_routes.player_runtime_service.latest, static_provider=provider),
        static_provider=provider,
        runtime_reader=None,
    )
    app = FastAPI()
    app.include_router(navigation_routes.router)
    return TestClient(app)


def test_radar_grid_accepts_arbitrary_center_and_keeps_coordinate_semantics():
    client = _client(RadarProvider())
    try:
        response = client.get("/api/v1/navigation/radar/grid?zone_id=445&x=1&y=2&z=3&radius=1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["format"] == "black2-spatial-grid-radar/v2"
        assert payload["center"] == {"x": 1, "y": 2, "z": 3}
        assert payload["bounds"] == {"min_x": 0, "max_x": 2, "min_z": 2, "max_z": 4, "width": 3, "height": 3}
        assert payload["grid"][0][0]["x"] == 0
        assert payload["grid"][0][0]["z"] == 2
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(navigation_routes.observed_navigation_graph, lambda: navigation_routes.player_runtime_service.latest)
        )


def test_zone_area_defaults_to_fuzzy_and_preserves_critical_symbols_and_flags():
    client = _client(RadarProvider())
    try:
        response = client.get("/api/v1/navigation/radar/area?zone_id=445&mode=fuzzy&block_size=2&max_cells=100")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["scope"] == "zone"
        assert payload["mode"] == "fuzzy"
        assert payload["block_size"] == 2
        flat = [cell for row in payload["grid"] for cell in row]
        assert any(cell["symbol"] == "D" and "D" in cell["contains"] for cell in flat)
        assert any(cell["symbol"] == "N" and "N" in cell["contains"] for cell in flat)
        assert any(cell["symbol"] in {"↑", "↓", "←", "→", "↕"} and cell["directional"]["one_way"] for cell in flat)
        assert any(cell["walkable_all"] is False and cell["blocked_ratio"] > 0 for cell in flat)
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(navigation_routes.observed_navigation_graph, lambda: navigation_routes.player_runtime_service.latest)
        )


def test_directional_radar_reports_relative_left_and_one_way_ledge():
    client = _client(RadarProvider())
    try:
        payload = client.get("/api/v1/navigation/radar/directional?range=2&zone_id=445&x=2&y=2&z=2").json()
        assert payload["format"] == "black2-directional-radar/v2"
        assert payload["relative_to_facing"]["ahead"]["axis"] == "X-"
        assert payload["cardinal_directions"]["left_west"]["cells"][0]["x"] == 1
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(navigation_routes.observed_navigation_graph, lambda: navigation_routes.player_runtime_service.latest)
        )


def test_ledge_tiles_are_retained_as_directed_navigation_cells():
    graph = object.__new__(RomStaticNavigationGraph)
    graph._lock = __import__("threading").RLock()
    from collections import OrderedDict
    graph._layer_cache = OrderedDict()
    graph._layer_cache_cap = 4

    def surfaces(_zone_id):
        def item(x, tile_class, material, static_blocked, ledge=None, blocked_dirs=()):
            return {"x": x, "z": 0, "layer_index": 0, "chunk_id": 0, "local_x": x, "local_z": 0,
                    "surface": {"raw": {"tile_class": tile_class, "flags": 1 if static_blocked else 0},
                                "collision": {"static_blocked": static_blocked, "ledge_direction": ledge, "blocked_directions": list(blocked_dirs)},
                                "material": material, "height": {}}}
        return [
            item(0, 31, {"kind": "ground", "status": "verified"}, False),
            item(1, 0x72, {"kind": "ledge", "status": "verified"}, True, ledge="right"),
            item(2, 31, {"kind": "ground", "status": "verified"}, False),
        ]

    graph._zone_surfaces = surfaces
    cells = graph._cells_for_layer(445, 2)
    assert (1, 0) in cells
    assert cells[(1, 0)].ledge_direction == "right"
    neighbors = list(RomStaticNavigationGraph._neighbors(cells, cells[(1, 0)], set()))
    assert [(item.node.x, item.node.z) for item in neighbors] == [(2, 0)]


def test_fuzzy_aggregation_retains_walkability_summary_and_directional_exits():
    cells = [[
        {"x": 0, "z": 0, "symbol": ".", "walkable": True, "events": [], "directional": {"allowed_exits": ["right"]}},
        {"x": 1, "z": 0, "symbol": "#", "walkable": False, "events": [], "directional": {"allowed_exits": []}},
    ]]
    result = _aggregate_fuzzy(cells, 2)[0][0]
    assert result["walkable"] is True
    assert result["walkable_all"] is False
    assert result["blocked_ratio"] == 0.5
    assert result["directional"]["allowed_exits"] == ["right"]



def test_world_index_and_matrix_map_are_explicitly_bounded_by_matrix_domain():
    client = _client(RadarProvider())
    try:
        index = client.get("/api/v1/navigation/radar/world/index").json()
        assert index["format"] == "black2-world-radar-index/v1"
        assert index["matrices"][0]["matrix_id"] == 255
        assert 0xFFFFFFFF not in index["matrices"][0].get("zone_ids", [])

        world = client.get("/api/v1/navigation/radar/world/map?matrix_id=255&mode=fuzzy&block_size=2&max_cells=100").json()
        assert world["format"] == "black2-world-radar-map/v1"
        assert world["coordinate_policy"].startswith("This is one Matrix domain")
        assert world["center"] == {"x": 2, "y": 2, "z": 2}
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(navigation_routes.observed_navigation_graph, lambda: navigation_routes.player_runtime_service.latest)
        )


def test_all_gen5_ledge_tile_classes_expose_directional_semantics():
    for tile_class, direction in ((0x72, "right"), (0x73, "left"), (0x74, "up"), (0x75, "down")):
        kind, symbol, _status = _classify_surface_meta(
            tile_class, 1, False, True, ledge_direction=direction, material={"kind": "ledge"}
        )
        assert kind.startswith("单向跳台")
        assert symbol == {"right": "→", "left": "←", "up": "↑", "down": "↓"}[direction]


def test_matrix_fuzzy_map_uses_bounded_sampled_blocks():
    client = _client(RadarProvider())
    try:
        response = client.get("/api/v1/navigation/radar/world/map?matrix_id=255&mode=fuzzy&block_size=2&max_cells=100")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["grid"][0][0]["sampling"]["strategy"] == "fuzzy-block-sampled"
        assert payload["grid"][0][0]["sampling"]["complete"] is False
        assert payload["grid"][0][0]["bounds"]["width"] <= 2
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(navigation_routes.observed_navigation_graph, lambda: navigation_routes.player_runtime_service.latest)
        )


def test_world_atlas_exposes_matrix_domains_without_fake_flattening():
    client = _client(RadarProvider())
    try:
        response = client.get("/api/v1/navigation/radar/world/atlas")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["format"] == "black2-world-radar-atlas/v1"
        assert payload["render_policy"]["single_raster"] is False
        assert payload["matrix_nodes"][0]["matrix_id"] == 255
        assert payload["connectors"] == []
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(navigation_routes.observed_navigation_graph, lambda: navigation_routes.player_runtime_service.latest)
        )



def test_unresolved_live_radar_never_falls_back_to_old_ranch_coordinates():
    provider = RadarProvider()
    client = _client(provider)
    try:
        navigation_routes.player_runtime_service.latest = {
            "status": "unresolved", "confidence": "unresolved",
            "reason": "test unresolved runtime",
        }
        response = client.get("/api/v1/navigation/radar/grid?radius=1")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RADAR_PLAYER_UNRESOLVED"
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(navigation_routes.observed_navigation_graph, lambda: navigation_routes.player_runtime_service.latest)
        )


def test_ledge_symbol_points_in_allowed_direction():
    for direction, expected in (("right", "→"), ("left", "←"), ("up", "↑"), ("down", "↓")):
        _kind, symbol, _status = _classify_surface_meta(
            0x72, 1, True, True, ledge_direction=direction, material={"kind": "ledge"}
        )
        assert symbol == expected


def test_radar_interactions_exposes_npc_and_object_affordances():
    client = _client(RadarProvider())
    try:
        response = client.get("/api/v1/navigation/radar/interactions?zone_id=445&x=2&y=2&z=2&radius=2")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["format"] == "black2-radar-interactions/v1"
        assert any(row["events"] for row in payload["interactions"])
        assert "npc_movement_policy" in payload
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(navigation_routes.observed_navigation_graph, lambda: navigation_routes.player_runtime_service.latest)
        )


def test_blocked_terrain_semantics_can_still_be_interactable():
    kind, symbol, status = _classify_surface_meta(
        0xD6, 1, True, False, material={"kind": "pc", "interaction": "pc", "label": "PC terminal"}
    )
    assert kind.startswith("宝可梦电脑")
    assert symbol == "C"
    assert "交互" in status


def test_warp_coordinates_use_floor_and_expose_facility_metadata():
    provider = RadarProvider()
    client = _client(provider)
    try:
        response = client.get("/api/v1/navigation/radar/grid?zone_id=445&x=2&y=2&z=0&radius=1")
        assert response.status_code == 200, response.text
        payload = response.json()
        door_cells = [cell for row in payload["grid"] for cell in row if cell.get("symbol") == "D"]
        assert len(door_cells) >= 1
        door = door_cells[0]
        assert door["interactable"] is True
        assert door["interaction"]["action"] == "enter_warp"
        assert "target_zone_name" in door["interaction"]
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(navigation_routes.observed_navigation_graph, lambda: navigation_routes.player_runtime_service.latest)
        )


def test_signpost_spans_three_tiles_and_requires_front_middle_interaction():
    client = _client(RadarProvider())
    try:
        response = client.get("/api/v1/navigation/radar/interactions?zone_id=445&x=2&y=2&z=2&radius=2")
        assert response.status_code == 200, response.text
        payload = response.json()
        signs = [item for item in payload["interactions"] if item.get("symbol") == "S"]
        assert len(signs) >= 1
        sign = signs[0]
        assert sign["interaction"]["action"] == "read_signpost"
        assert sign["interaction"]["interaction_rule"] == "front_middle_only"
        assert sign["interaction"]["stand_tile"]["x"] == 3
        assert sign["interaction"]["stand_tile"]["z"] == 2
        assert sign["interaction"]["stand_tile"]["facing"] == "up"
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(navigation_routes.observed_navigation_graph, lambda: navigation_routes.player_runtime_service.latest)
        )


def test_water_edge_is_classified_as_surf_shore_transition():
    kind, symbol, status = _classify_surface_meta(
        0x41, 131, True, False, material={"kind": "water_edge", "label": "Lake shore", "interaction": "surf_edge"}
    )
    assert symbol == "~"
    assert "水岸" in kind
    assert "冲浪" in status


def test_zone_446_hiker_story_gate_is_classified_with_passable_east():
    from backend.black2.api.navigation_routes import _is_story_gate_npc
    is_gate, info = _is_story_gate_npc(446, 4, 735, 64)
    assert is_gate is True
    assert info["blocked_direction"] == "North"
    assert info["passable_direction"] == "East"
    assert info["npc_tile"] == {"x": 159, "z": 645, "y": 2}
    assert "基础徽章" in info["condition"]
    assert len(info["intercept_trigger_tiles"]) == 2


def test_trainer_sight_rays_projection_and_occlusion():
    """Verify active trainers project directional sight rays [^, v, <, >] and occlude at obstacles."""
    from backend.black2.api.navigation_routes import _build_trainer_sight_rays
    from backend.black2.world.static_navigation import RomStaticNavigationGraph

    provider = RomStaticNavigationGraph()
    # Zone 445 has Trainer 4 at (13, 48) facing North with sight 3
    sights, trainers, summaries = _build_trainer_sight_rays(provider, 445, 10, 20, 40, 52)
    assert (13, 48) in trainers
    tr = trainers[(13, 48)]
    assert tr["symbol"] == "T"
    assert tr["facing"] == "North"
    assert tr["sight_range"] == 3

    # Check sight rays at (13, 47), (13, 46), (13, 45)
    for st, z_coord in enumerate((47, 46, 45), 1):
        assert (13, z_coord) in sights
        ray = sights[(13, z_coord)]
        assert ray["symbol"] == "^"
        assert ray["step"] == st
        assert ray["max_distance"] == 3
        assert ray["hazard"] == "trainer_sight_battle"

    # Beyond sight range, (13, 44) must NOT be a sight tile
    assert (13, 44) not in sights

    # Zone 446 has Trainer 1 at (154, 651) facing East with sight 4, blocked by cliff at (159, 651)
    sights_446, trainers_446, summaries_446 = _build_trainer_sight_rays(provider, 446, 150, 162, 645, 655)
    assert (154, 651) in trainers_446
    tr_446 = trainers_446[(154, 651)]
    assert tr_446["facing"] == "East"
    assert tr_446["symbol"] == "T"
    for st, x_coord in enumerate((155, 156, 157, 158), 1):
        assert (x_coord, 651) in sights_446
        assert sights_446[(x_coord, 651)]["symbol"] == ">"
        assert sights_446[(x_coord, 651)]["step"] == st

    # Obstacle at (159, 651) prevents ray from extending
    assert (159, 651) not in sights_446


def test_trainer_radar_grid_endpoint_and_tactical_card():
    """Verify radar grid endpoint exposes [T] and [^] with tactical card in text_map."""
    from backend.black2.world.static_navigation import RomStaticNavigationGraph

    provider = RomStaticNavigationGraph()
    navigation_routes.configure_navigation_routes(
        NavigationPlanService(ObservedNavigationGraph(), lambda: None, static_provider=provider),
        static_provider=provider,
        runtime_reader=None,
    )
    app = FastAPI()
    app.include_router(navigation_routes.router)
    client = TestClient(app)

    # 1. JSON radar grid
    resp = client.get("/api/v1/navigation/radar/grid?zone_id=445&x=13&y=0&z=48&radius=3")
    assert resp.status_code == 200
    data = resp.json()
    cell_map = {(c["x"], c["z"]): c for row in data["grid"] for c in row}

    # Trainer tile (13, 48)
    t_cell = cell_map[(13, 48)]
    assert t_cell["symbol"] == "T"
    assert t_cell["movement_hazard"] == "trainer_npc_battle"
    assert t_cell["walkable"] is False

    # Ray tiles (13, 47), (13, 46), (13, 45)
    for z_coord in (47, 46, 45):
        r_cell = cell_map[(13, z_coord)]
        assert r_cell["symbol"] == "^"
        assert r_cell["movement_hazard"] == "trainer_sight_battle"
        assert r_cell["trainer_sight"]["max_distance"] == 3

    # 2. Text map with tactical card
    text_resp = client.get("/api/v1/navigation/radar/grid?zone_id=445&x=13&y=0&z=48&radius=3&text_map=true")
    assert text_resp.status_code == 200
    text = text_resp.text
    assert "训练家对战视线警戒 (Active Trainer Sights):" in text
    assert "(X= 13, Z= 48) [T] 对战训练家" in text
    assert "视线警戒射线: (X=13, Z=47) ➔ (X=13, Z=45)" in text
    assert "[T] 对战训练家：未击败时具有对战视线" in text
    assert "[^] 训练家对战视线 (向北)" in text


def test_player_on_trainer_sight_ray_preserves_p_with_hazard():
    """Verify player on a sight tile remains [P] visually while hazard is attached."""
    from backend.black2.world.static_navigation import RomStaticNavigationGraph

    provider = RomStaticNavigationGraph()
    mock_player = {
        "status": "resolved", "confidence": "verified", "zone_id": 445,
        "position": {"grid": {"x": 13, "y": 0, "z": 47}},
        "orientation": {"facing": "South", "facing_zh": "南"},
    }
    navigation_routes.player_runtime_service.latest = mock_player
    navigation_routes.configure_navigation_routes(
        NavigationPlanService(ObservedNavigationGraph(), lambda: navigation_routes.player_runtime_service.latest, static_provider=provider),
        static_provider=provider,
        runtime_reader=None,
    )
    app = FastAPI()
    app.include_router(navigation_routes.router)
    client = TestClient(app)

    resp = client.get("/api/v1/navigation/radar/grid?zone_id=445&x=13&y=0&z=47&radius=2")
    assert resp.status_code == 200
    data = resp.json()
    cell_map = {(c["x"], c["z"]): c for row in data["grid"] for c in row}

    p_cell = cell_map[(13, 47)]
    assert p_cell["symbol"] == "P"
    assert p_cell["movement_hazard"] == "trainer_sight_battle"
    assert p_cell["trainer_sight"]["step"] == 1


def test_zone_name_official_rom_decoding_and_cave_environment():
    """Verify zone name decodes real ROM location name and cave environment attribute."""
    from backend.black2.world.static_navigation import RomStaticNavigationGraph
    from backend.black2.api.navigation_routes import _resolve_zone_name

    provider = RomStaticNavigationGraph()
    # Zone 335: Mistralton Cave - Chamber of Guidance (Cave)
    name_335 = _resolve_zone_name(335, provider)
    assert "引导之间" in name_335
    assert "吹寄洞穴" in name_335
    assert "[洞穴内 / Cave]" in name_335

    # Zone 439: Floccesy Town (Outdoor)
    name_439 = _resolve_zone_name(439, provider)
    assert "算木镇" in name_439
    assert "[室外 / Outdoor]" in name_439


def test_strength_boulder_filled_hole_and_walkability():
    """Verify Strength boulder in hole is recognized as filled walkable path [=]."""
    from backend.black2.world.static_navigation import RomStaticNavigationGraph
    from backend.black2.api.navigation_routes import _scan_boulder_mechanics, _radar_cell

    provider = RomStaticNavigationGraph()
    # Mock live RAM actors with a pushed boulder at (15, 17, y=-2)
    mock_boulder_actors = [{
        "actor_uid": 1,
        "model_id": 8197,
        "script_id": 10000,
        "grid": {"x": 15, "y": -2, "z": 17},
        "facing": "South",
    }]

    boulder_cells, active_boulders, summaries = _scan_boulder_mechanics(provider, 335, mock_boulder_actors)
    assert len(active_boulders) == 1
    assert active_boulders[0]["state"] == "filled"

    # All 4 tiles of 2x2 hole (15..16, 16..17) must be filled
    for hx in (15, 16):
        for hz in (16, 17):
            assert (hx, hz) in boulder_cells
            assert boulder_cells[(hx, hz)]["symbol"] == "="
            assert boulder_cells[(hx, hz)]["walkable"] is True

    # Test cell rendering through _radar_cell
    cell = _radar_cell(provider, 335, 15, 0, 17, boulder_cells=boulder_cells)
    assert cell["symbol"] == "="
    assert cell["walkable"] is True
    assert cell["movement_allowed"] is True
    assert "已填平巨石路面" in cell["kind"]
