"""High-level player runtime state built from verified Gen-V field structures.

The raw structure reader lives in runtime_field_resolver.py.  This service adds
only temporal facts that require two or more frames (velocity and foot-gait
calibration).  It never infers walk/run from MotionDir or an unnamed flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

from ..memory.reader import MemoryReader
from .runtime_field_resolver import RuntimeFieldLocator


@dataclass
class PlayerRuntimeService:
    locator: RuntimeFieldLocator = field(default_factory=RuntimeFieldLocator)
    previous_frame: int | None = None
    previous_world: dict[str, float] | None = None
    latest: dict[str, Any] | None = None
    walk_samples: list[float] = field(default_factory=list)
    run_samples: list[float] = field(default_factory=list)
    calibration_path: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "runtime" / "player_state_calibration.json"
    )

    def __post_init__(self) -> None:
        self._load_calibration()

    def _load_calibration(self) -> None:
        try:
            data = json.loads(self.calibration_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        self.walk_samples = [float(v) for v in data.get("walk_speed_world_units_per_frame", []) if float(v) > 0][-20:]
        self.run_samples = [float(v) for v in data.get("run_speed_world_units_per_frame", []) if float(v) > 0][-20:]

    def _save_calibration(self) -> None:
        self.calibration_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": "black2-player-gait-calibration/v1",
            "basis": "observed FieldActor.WPos displacement divided by exact BizHawk frame delta",
            "walk_speed_world_units_per_frame": self.walk_samples[-20:],
            "run_speed_world_units_per_frame": self.run_samples[-20:],
            "profile": self.calibration_profile(),
        }
        self.calibration_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def calibration_profile(self) -> dict[str, Any]:
        walk = median(self.walk_samples) if self.walk_samples else None
        run = median(self.run_samples) if self.run_samples else None
        threshold = None
        valid = bool(walk is not None and run is not None and run > walk * 1.10)
        if valid:
            threshold = (walk + run) / 2.0
        return {
            "status": "ready" if valid else "needs_samples",
            "walk_sample_count": len(self.walk_samples),
            "run_sample_count": len(self.run_samples),
            "walk_median": walk,
            "run_median": run,
            "decision_threshold": threshold,
            "rule": "speed <= threshold => Walking; speed > threshold => Running" if valid else None,
        }

    def reset_calibration(self) -> dict[str, Any]:
        self.walk_samples.clear()
        self.run_samples.clear()
        self._save_calibration()
        return self.calibration_profile()

    def record_gait_sample(self, label: str) -> dict[str, Any]:
        label = label.strip().lower()
        if label not in {"walk", "run"}:
            return {"ok": False, "reason": "label must be walk or run", "profile": self.calibration_profile()}
        latest = self.latest or {}
        locomotion = latest.get("locomotion", {})
        temporal = latest.get("temporal", {})
        speed = temporal.get("horizontal_speed_world_units_per_frame")
        if locomotion.get("transport_mode") != "OnFoot" or locomotion.get("phase") != "Moving":
            return {"ok": False, "reason": "current player is not moving on foot", "profile": self.calibration_profile()}
        if not isinstance(speed, (int, float)) or speed <= 0:
            return {"ok": False, "reason": "no valid frame-to-frame speed is available yet", "profile": self.calibration_profile()}
        target = self.walk_samples if label == "walk" else self.run_samples
        target.append(float(speed))
        del target[:-20]
        self._save_calibration()
        return {"ok": True, "label": label, "recorded_speed": speed, "profile": self.calibration_profile()}

    def _apply_temporal(self, sample: dict[str, Any]) -> dict[str, Any]:
        frame = sample.get("frame")
        world = ((sample.get("position") or {}).get("world") or {})
        current = {
            "x": world.get("x"),
            "y": world.get("y"),
            "z": world.get("z"),
        }
        frame_delta = None
        dx = dy = dz = speed = horizontal = None
        if (
            isinstance(frame, int) and self.previous_frame is not None and frame > self.previous_frame
            and self.previous_world is not None
            and all(isinstance(current.get(k), (int, float)) for k in ("x", "y", "z"))
        ):
            frame_delta = frame - self.previous_frame
            dx = float(current["x"]) - float(self.previous_world["x"])
            dy = float(current["y"]) - float(self.previous_world["y"])
            dz = float(current["z"]) - float(self.previous_world["z"])
            speed = math.sqrt(dx * dx + dy * dy + dz * dz) / frame_delta
            horizontal = math.sqrt(dx * dx + dz * dz) / frame_delta

        if isinstance(frame, int) and all(isinstance(current.get(k), (int, float)) for k in ("x", "y", "z")):
            self.previous_frame = frame
            self.previous_world = {k: float(current[k]) for k in current}

        sample["temporal"] = {
            "frame_delta": frame_delta,
            "delta_world": {"x": dx, "y": dy, "z": dz},
            "speed_world_units_per_frame": speed,
            "horizontal_speed_world_units_per_frame": horizontal,
            "speed_tiles_per_frame": (horizontal / 16.0) if isinstance(horizontal, (int, float)) else None,
            "source": "FieldActor.WPos across exact bridge frame-stamped samples",
        }

        locomotion = sample.setdefault("locomotion", {})
        profile = self.calibration_profile()
        locomotion["gait_calibration"] = profile
        if locomotion.get("transport_mode") == "OnFoot" and locomotion.get("phase") == "Moving":
            threshold = profile.get("decision_threshold")
            if isinstance(horizontal, (int, float)) and isinstance(threshold, (int, float)):
                locomotion["gait"] = "Running" if horizontal > threshold else "Walking"
                locomotion["gait_confidence"] = "calibrated"
                locomotion["semantic_state"] = "Running (跑步)" if locomotion["gait"] == "Running" else "Walking (走路)"
            else:
                locomotion["gait"] = "UnresolvedWalkVsRun"
                locomotion["gait_confidence"] = "needs_walk_and_run_calibration"
        return sample

    def invalidate(self) -> None:
        self.latest = None
        self.locator.invalidate()

    async def sample(self, reader: MemoryReader, *, allow_discovery: bool = False) -> dict[str, Any]:
        """Sample cached player structures without scheduling RAM-wide discovery by default."""
        sample = await self.locator.sample_player(reader, allow_discovery=allow_discovery)
        if sample.get("status") not in {"resolved", "candidate"}:
            self.latest = sample
            return sample
        sample = self._apply_temporal(sample)
        self.latest = sample
        return sample


player_runtime_service = PlayerRuntimeService()
