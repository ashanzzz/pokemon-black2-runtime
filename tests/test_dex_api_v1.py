"""Offline Black 2 Dex API contracts and representative data checks."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.api.dex_routes import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_summary_is_versioned_and_complete():
    body = _client().get("/api/v1/dex/summary")
    assert body.status_code == 200
    value = body.json()
    assert value["format"] == "black2-dex-summary/v1"
    assert value["dataset"]["source"]["commit"] == "d4f9a4af58ade123fbc0558f68b1c69daa97d9e4"
    assert value["dataset"]["source"]["runtime_network_required"] is False
    assert value["dataset"]["game"]["version_group_id"] == 14
    assert value["dataset"]["id_space_ranges"]["pokemon"] == {
        "minimum": 1,
        "maximum": 649,
        "key": "national_dex",
    }
    assert value["dataset"]["rom_provenance"]["status"] == "external_local_rom_reference"
    assert value["dataset"]["rom_id_spaces"]["types"]["range"] == {
        "minimum": 0,
        "maximum": 16,
    }
    assert value["counts"] == {
        "pokemon": 649,
        "moves": 559,
        "items": 623,
        "abilities": 164,
        "types": 17,
        "machines": 101,
        "learnset_entries": 40028,
    }


def test_national_dex_edges_have_bilingual_names_and_gen5_stats():
    client = _client()
    first = client.get("/api/v1/dex/pokemon/1")
    last = client.get("/api/v1/dex/pokemon/649")
    assert first.status_code == last.status_code == 200
    bulbasaur = first.json()["pokemon"]
    genesect = last.json()["pokemon"]
    assert bulbasaur["names"] == {"en": "Bulbasaur", "zh-Hans": "妙蛙种子"}
    assert genesect["names"] == {"en": "Genesect", "zh-Hans": "盖诺赛克特"}
    assert {row["identifier"] for row in bulbasaur["types"]} == {"grass", "poison"}
    assert {row["identifier"] for row in bulbasaur["abilities"]} == {"overgrow", "chlorophyll"}
    assert {row["identifier"]: row["base_stat"] for row in bulbasaur["base_stats"]} == {
        "hp": 45,
        "attack": 49,
        "defense": 49,
        "special-attack": 65,
        "special-defense": 65,
        "speed": 45,
    }
    assert bulbasaur["learnset"]["version_group_id"] == 14
    assert any(row["move_id"] == 33 and row["method"]["identifier"] == "level-up" for row in bulbasaur["learnset"]["entries"])


def test_move_33_uses_black2_numeric_values_and_learnset_machine_links():
    client = _client()
    response = client.get("/api/v1/dex/moves/33")
    assert response.status_code == 200
    move = response.json()["move"]
    assert move["names"] == {"en": "Tackle", "zh-Hans": "撞击"}
    assert move["power"] == 50  # current PokeAPI is 40; Gen V changelog restores 50
    assert move["accuracy"] == 100
    assert move["pp"] == 35
    assert move["type"]["identifier"] == "normal"
    assert move["damage_class"]["identifier"] == "physical"
    assert any(entry["pokemon_id"] == 1 for entry in move["learned_by"])


def test_item_pokeapi_and_generation5_ids_are_both_exposed():
    client = _client()
    potion = client.get("/api/v1/dex/items/17")
    assert potion.status_code == 200
    item = potion.json()["item"]
    assert item["names"] == {"en": "Potion", "zh-Hans": "伤药"}
    assert item["game_index"] == 17
    assert item["game_indices"] == [17]
    assert item["price"]["pokeapi_cost"] == 200

    ambiguous = client.get("/api/v1/dex/items/by-game-index/227")
    assert ambiguous.status_code == 200
    value = ambiguous.json()
    assert value["ambiguous"] is True
    assert {entry["id"] for entry in value["items"]} == {203, 204}


def test_lists_support_name_id_pagination_and_missing_detail_is_404():
    client = _client()
    result = client.get("/api/v1/dex/pokemon", params={"q": "#649", "limit": 1})
    assert result.status_code == 200
    value = result.json()
    assert value["pagination"]["matched"] == 1
    assert value["items"][0]["id"] == 649
    padded = client.get("/api/v1/dex/pokemon", params={"q": "#001", "limit": 1})
    assert padded.json()["items"][0]["id"] == 1
    assert client.get("/api/v1/dex/moves/9999").status_code == 404
    assert client.get("/api/v1/dex/items/9999").status_code == 404


def test_types_and_unified_catalog_compatibility_surface():
    client = _client()
    type_response = client.get("/api/v1/dex/types/17")
    assert type_response.status_code == 200
    dark = type_response.json()["type"]
    assert dark["names"] == {"en": "Dark", "zh-Hans": "恶"}
    assert len(dark["efficacy_vs"]) == 17
    assert dark["rom_id"] == 16

    # The full application registers the compatibility router separately;
    # exercise it in the same lightweight app used by this contract suite.
    from backend.black2.api.catalog_routes import router as catalog_router

    catalog_app = FastAPI()
    catalog_app.include_router(catalog_router)
    catalog = TestClient(catalog_app)
    caps = catalog.get("/api/v1/catalog/capabilities")
    assert caps.status_code == 200
    assert caps.json()["entities"]["types"]["count"] == 17
    search = catalog.get("/api/v1/catalog/search", params={"q": "tackle"})
    assert search.status_code == 200
    assert any(row["entity"] == "moves" and row["id"] == 33 for row in search.json()["results"])
    detail = catalog.get("/api/v1/catalog/abilities/164")
    assert detail.status_code == 200
    assert detail.json()["ability"]["identifier"] == "teravolt"
    rom = catalog.get("/api/v1/catalog/rom/items/17")
    assert rom.status_code == 200
    assert rom.json()["items"][0]["identifier"] == "potion"
    move_rom = catalog.get("/api/v1/catalog/moves", params={"rom_id": 33})
    assert move_rom.status_code == 200
    assert move_rom.json()["pagination"]["matched"] == 1
    assert move_rom.json()["items"][0]["identifier"] == "tackle"
