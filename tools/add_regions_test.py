with open("tests/test_story_gym7_and_fly_api.py", "r", encoding="utf-8") as f:
    text = f.read()

new_test = """

def test_flyable_regions_full_catalog_and_filtering(client):
    mock_p = {
        "status": "resolved",
        "zone_id": 406,
        "position": {"grid": {"x": 660, "y": 0, "z": 186}},
        "locomotion": {"phase": "Idle"},
    }
    with patch.object(player_runtime_service, "latest", mock_p), \
         patch("backend.black2.api.battle_routes._party_decoder.sample", new_callable=AsyncMock) as mock_party:
        mock_party.return_value = {
            "slots": [{"slot": 6, "species": 169, "species_name": "Crobat", "level": 41, "moves": [{"move_id": 19}]}]
        }
        res = client.get("/api/v1/player/fly/regions")
        assert res.status_code == 200
        body = res.json()
        assert body["format"] == "black2-flyable-regions/v1"
        assert body["total_destinations"] == 58
        assert body["current_flight_status"]["can_fly_now"] is True
        assert body["current_flight_status"]["flying_mount"]["slot"] == 6
        assert "gym_cities" in body["categories_summary"]

        # Test filtering by category=gym_cities
        res_gyms = client.get("/api/v1/player/fly/destinations?category=gym_cities&format=flat")
        assert res_gyms.status_code == 200
        gym_list = res_gyms.json()
        assert len(gym_list) >= 8
        assert all(g["has_gym"] is True for g in gym_list)

        # Test search query
        res_search = client.get("/api/v1/player/fly/destinations?search=120&format=flat")
        assert res_search.status_code == 200
        assert len(res_search.json()) >= 1
        assert res_search.json()[0]["zone_id"] == 120
"""

if "def test_flyable_regions_full_catalog_and_filtering" not in text:
    text += new_test
    with open("tests/test_story_gym7_and_fly_api.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Added test_flyable_regions_full_catalog_and_filtering!")
else:
    print("Test already present.")