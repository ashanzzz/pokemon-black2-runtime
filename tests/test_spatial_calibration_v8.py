from pathlib import Path
from tempfile import TemporaryDirectory

from backend.black2.world.spatial_calibration import SpatialCalibrationService


def p(frame,x,y,z):
    return {"status":"resolved","frame":frame,"zone_id":1,"grid":{"x":x,"y":y,"z":z},"world":{"x":x*16+8,"y":y*16.0,"z":z*16+8},"validation":{"residual_world":{"x":0.0,"z":0.0},"chunk_matches_gpos":True,"facing_crosscheck":True},"orientation":{"facing":"South"},"locomotion":{"phase":"Idle"}}


def test_calibration_exports_zip():
    with TemporaryDirectory() as td:
        svc=SpatialCalibrationService(project_root=Path(td));svc.start("bridge-test","bridge")
        svc.sample(p(1,1,2,1),{"scene_key":"z1","environment":"exterior","static":{"buildings":[]}})
        svc.sample(p(2,2,3,1),{"scene_key":"z1","environment":"exterior","static":{"buildings":[]}})
        out=svc.finish(renderer_diagnostics={"fps":30,"buildings_failed":1})
        assert out["ok"] is True
        assert (svc.out_dir/out["zip_name"]).is_file()
        assert out["summary"]["observed_elevation_changes"]==1
