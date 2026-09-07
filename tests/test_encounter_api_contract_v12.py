from backend.black2.api.encounter_routes import router
from backend.black2.runtime.versions import PROTOCOL_VERSIONS


def test_encounter_router_exposes_region_profile_search_and_task_contracts():
    routes = {(route.path, frozenset(route.methods or ())) for route in router.routes}
    assert ("/api/v1/encounters/capabilities", frozenset({"GET"})) in routes
    assert ("/api/v1/encounters/regions", frozenset({"GET"})) in routes
    assert ("/api/v1/encounters/regions/current", frozenset({"GET"})) in routes
    assert ("/api/v1/encounters/regions/{region_id}", frozenset({"GET"})) in routes
    assert ("/api/v1/encounters/profiles", frozenset({"GET"})) in routes
    assert ("/api/v1/encounters/search", frozenset({"GET"})) in routes
    assert ("/api/v1/encounters/tasks", frozenset({"POST"})) in routes
    assert ("/api/v1/encounters/tasks/{task_id}", frozenset({"GET"})) in routes
    assert ("/api/v1/encounters/tasks/{task_id}/cancel", frozenset({"POST"})) in routes


def test_encounter_protocol_versions_are_discoverable():
    protocols = {item["id"]: item["version"] for item in PROTOCOL_VERSIONS}
    assert protocols["encounter_regions"] == "black2-encounter-regions/v1"
    assert protocols["encounter_tasks"] == "black2-encounter-task/v1"
