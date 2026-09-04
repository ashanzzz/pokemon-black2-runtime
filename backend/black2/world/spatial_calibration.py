"""Spatial calibration/evidence sessions for the 3D workbench.

The operator can record an outdoor, indoor, bridge, stairs, door or generic
walk session.  Samples come from already-cached PlayerRuntime data, so recording
itself does not add full-RAM scans.  Each sample also feeds the observed layered
navigation graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path
import re
import statistics
import threading
from typing import Any
import zipfile

from .observed_navigation import observed_navigation_graph


def _clean(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value.strip())[:64] or "calibration"


def _distance_xz(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    try:
        return math.hypot(float(a["x"]) - float(b["x"]), float(a["z"]) - float(b["z"]))
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class SpatialCalibrationService:
    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[3])
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _session: dict[str, Any] | None = None

    @property
    def out_dir(self) -> Path:
        return self.project_root / "runtime" / "calibration_reports"

    def start(self, label: str, scenario: str = "general") -> dict[str, Any]:
        now = datetime.now()
        with self._lock:
            observed_navigation_graph.reset_trace()
            self._session = {
                "format": "black2-spatial-calibration-session/v1",
                "session_id": now.strftime("%Y%m%d_%H%M%S_%f"),
                "label": label.strip() or scenario,
                "scenario": scenario,
                "started_at": now.isoformat(timespec="milliseconds"),
                "samples": [],
                "renderer_diagnostics": None,
                "notes": [],
            }
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            s = self._session
            if not s:
                return {"active": False, "sample_count": 0}
            return {
                "active": True,
                "session_id": s["session_id"],
                "label": s["label"],
                "scenario": s["scenario"],
                "started_at": s["started_at"],
                "sample_count": len(s["samples"]),
            }

    def sample(self, player: dict[str, Any], scene: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if not self._session:
                return {"ok": False, "reason": "no active calibration session"}
            if player.get("status") not in {"resolved", "candidate"}:
                return {"ok": False, "reason": "player runtime unresolved"}
            static = (scene or {}).get("static") or {}
            buildings = static.get("buildings") or []
            pworld = player.get("world") or {}
            nearest = []
            for item in buildings:
                dist = _distance_xz(pworld, item.get("world") or {})
                if dist is None:
                    continue
                nearest.append({
                    "id": item.get("id"), "uid": item.get("uid"), "door_uid": item.get("door_uid"),
                    "distance_xz_world": round(dist, 4), "world": item.get("world"),
                })
            nearest.sort(key=lambda x: x["distance_xz_world"])
            rec = {
                "index": len(self._session["samples"]),
                "captured_at": datetime.now().isoformat(timespec="milliseconds"),
                "frame": player.get("frame"),
                "zone_id": player.get("zone_id"),
                "scene_key": (scene or {}).get("scene_key"),
                "environment": (scene or {}).get("environment"),
                "grid": player.get("grid"),
                "world": player.get("world"),
                "chunk": player.get("chunk"),
                "orientation": player.get("orientation"),
                "locomotion": player.get("locomotion"),
                "validation": player.get("validation"),
                "nearest_buildings": nearest[:5],
            }
            self._session["samples"].append(rec)
            nav = observed_navigation_graph.observe_player(player, source=f"calibration:{self._session['scenario']}")
            return {"ok": True, "sample": rec, "navigation_observation": nav, "sample_count": len(self._session["samples"])}

    def finish(self, *, renderer_diagnostics: dict[str, Any] | None = None, notes: str | None = None) -> dict[str, Any]:
        with self._lock:
            if not self._session:
                return {"ok": False, "reason": "no active calibration session"}
            session = self._session
            self._session = None
        if renderer_diagnostics:
            session["renderer_diagnostics"] = renderer_diagnostics
        if notes:
            session["notes"].append(notes)
        session["finished_at"] = datetime.now().isoformat(timespec="milliseconds")
        summary = self._summarize(session)
        session["summary"] = summary
        return self._write_report(session)

    def _summarize(self, session: dict[str, Any]) -> dict[str, Any]:
        samples = session.get("samples") or []
        zones = []
        residual_x, residual_z, world_y, grid_y = [], [], [], []
        chunk_bad = 0
        facing_bad = 0
        for s in samples:
            z = s.get("zone_id")
            if isinstance(z, int) and z not in zones:
                zones.append(z)
            v = s.get("validation") or {}
            r = v.get("residual_world") or {}
            if isinstance(r.get("x"), (int, float)): residual_x.append(float(r["x"]))
            if isinstance(r.get("z"), (int, float)): residual_z.append(float(r["z"]))
            if v.get("chunk_matches_gpos") is False: chunk_bad += 1
            if v.get("facing_crosscheck") is False: facing_bad += 1
            w = s.get("world") or {}; g = s.get("grid") or {}
            if isinstance(w.get("y"), (int, float)): world_y.append(float(w["y"]))
            if isinstance(g.get("y"), int): grid_y.append(int(g["y"]))
        def stats(values: list[float]) -> dict[str, float] | None:
            if not values: return None
            return {"min": min(values), "max": max(values), "mean": statistics.fmean(values), "p95": sorted(values)[max(0, math.ceil(len(values)*0.95)-1)]}
        transitions = 0
        elevation_changes = 0
        last = None
        for s in samples:
            g = s.get("grid") or {}
            node = (s.get("zone_id"), g.get("x"), g.get("y"), g.get("z"))
            if last and node != last:
                transitions += 1
                if node[2] != last[2]: elevation_changes += 1
            last = node
        renderer = session.get("renderer_diagnostics") or {}
        return {
            "sample_count": len(samples),
            "zones": zones,
            "environments": sorted({str(s.get("environment")) for s in samples if s.get("environment")}),
            "grid_world_residual_x": stats(residual_x),
            "grid_world_residual_z": stats(residual_z),
            "world_y_range": stats(world_y),
            "grid_elevation_values": sorted(set(grid_y)),
            "observed_position_transitions": transitions,
            "observed_elevation_changes": elevation_changes,
            "chunk_mismatch_samples": chunk_bad,
            "facing_crosscheck_failures": facing_bad,
            "renderer": {
                "terrain_loaded": renderer.get("terrain_loaded"),
                "terrain_failed": renderer.get("terrain_failed"),
                "buildings_loaded": renderer.get("buildings_loaded"),
                "buildings_failed": renderer.get("buildings_failed"),
                "player_mode": renderer.get("player_mode"),
                "npc_original_count": renderer.get("npc_original_count"),
                "npc_fallback_count": renderer.get("npc_fallback_count"),
                "fps": renderer.get("fps"),
            },
            "navigation_graph": observed_navigation_graph.status(),
        }

    def _write_report(self, session: dict[str, Any]) -> dict[str, Any]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"calibration_{session['session_id']}_{_clean(session['scenario'])}_{_clean(session['label'])}"
        folder = self.out_dir / stem
        folder.mkdir(parents=True, exist_ok=True)
        json_path = folder / "calibration_report.json"
        md_path = folder / "CALIBRATION_REPORT.md"
        json_path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = session["summary"]
        md = [
            "# Pokémon Black 2 Spatial Calibration Report",
            "",
            f"- Session: `{session['session_id']}`",
            f"- Label: {session['label']}",
            f"- Scenario: `{session['scenario']}`",
            f"- Samples: {summary['sample_count']}",
            f"- Zones: {summary['zones']}",
            f"- Environments: {summary['environments']}",
            "",
            "## Coordinate checks",
            f"- Grid→World X residual: `{summary['grid_world_residual_x']}`",
            f"- Grid→World Z residual: `{summary['grid_world_residual_z']}`",
            f"- Chunk mismatch samples: **{summary['chunk_mismatch_samples']}**",
            f"- Facing crosscheck failures: **{summary['facing_crosscheck_failures']}**",
            f"- Grid elevation values: `{summary['grid_elevation_values']}`",
            f"- World Y range: `{summary['world_y_range']}`",
            "",
            "## Movement / layered navigation",
            f"- Position transitions: **{summary['observed_position_transitions']}**",
            f"- Elevation-changing transitions: **{summary['observed_elevation_changes']}**",
            f"- Observed graph: `{summary['navigation_graph']}`",
            "",
            "## Renderer diagnostics",
            f"```json\n{json.dumps(summary['renderer'], ensure_ascii=False, indent=2)}\n```",
            "",
            "This report intentionally distinguishes ROM/static facts, runtime observations and renderer failures.",
        ]
        md_path.write_text("\n".join(md), encoding="utf-8")
        zip_path = self.out_dir / f"{stem}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(json_path, arcname=f"{stem}/{json_path.name}")
            zf.write(md_path, arcname=f"{stem}/{md_path.name}")
        return {
            "ok": True,
            "session_id": session["session_id"],
            "summary": summary,
            "zip_name": zip_path.name,
            "download_url": f"/api/v1/lab/calibration/download/{zip_path.name}",
            "folder": str(folder),
        }

    def list_reports(self) -> dict[str, Any]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for path in sorted(self.out_dir.glob("calibration_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            rows.append({"name": path.name, "size_kb": round(path.stat().st_size/1024, 1), "download_url": f"/api/v1/lab/calibration/download/{path.name}"})
        return {"count": len(rows), "reports": rows}


spatial_calibration_service = SpatialCalibrationService()
