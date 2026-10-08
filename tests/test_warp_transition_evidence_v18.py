import json

from backend.black2.world.warp_transition_evidence import RuntimeWarpEvidenceStore


def test_warp_evidence_matches_json_array_grids_after_restart(tmp_path):
    path = tmp_path / "warp_transition_evidence.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "source_zone": 439,
                        "source_grid": [105, 1, 694],
                        "destination_zone": 443,
                        "landing_grid": [7, 0, 19],
                        "frame_before": 1,
                        "frame_after": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    store = RuntimeWarpEvidenceStore(path)
    matches = store.match(source_zone=439, source_x=105, source_z=694, destination_zone=443)

    assert len(matches) == 1
    assert matches[0]["source_grid"] == {"x": 105, "y": 1, "z": 694}
    assert matches[0]["landing_grid"] == {"x": 7, "y": 0, "z": 19}
