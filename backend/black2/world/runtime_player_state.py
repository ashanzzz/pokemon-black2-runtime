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
from ..runtime.events import agent_event_bus
from .runtime_field_resolver import RuntimeFieldLocator
from .warp_transition_evidence import WarpTransitionEvidence, runtime_warp_evidence


@dataclass
class PlayerRuntimeService:
    locator: RuntimeFieldLocator = field(default_factory=RuntimeFieldLocator)
    previous_frame: int | None = None
    previous_world: dict[str, float] | None = None
    latest: dict[str, Any] | None = None
    latest_session_id: str | None = None
    # Keep the last resolved sample across a short Field/Mapper locator miss.
    # A Zone transition commonly invalidates the old pointer chain for a few
    # frames; replacing this with an unresolved sample would erase the source
    # endpoint before the destination becomes readable.
    last_resolved: dict[str, Any] | None = None
    last_resolved_session_id: str | None = None
    last_refresh_failure: dict[str, Any] | None = None
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
        self.latest_session_id = None
        self.last_resolved = None
        self.last_resolved_session_id = None
        self.last_refresh_failure = None
        self.previous_frame = None
        self.previous_world = None
        self.locator.invalidate()

    @staticmethod
    def _transport_identity(reader: MemoryReader) -> tuple[str | None, bool | None]:
        """Return the bridge session and connection fact exposed by a reader.

        Minimal offline readers used by analysis tools do not necessarily carry
        a BridgeClient.  ``None`` therefore means unknown, not disconnected.
        A retained live cache is deliberately stricter: it requires a concrete
        matching session identifier.
        """
        client = getattr(reader, "client", None)
        transport = getattr(client, "transport", None)
        raw_session = getattr(transport, "session_id", None)
        session_id = raw_session if isinstance(raw_session, str) and raw_session else None
        connected = getattr(client, "is_connected", None)
        try:
            connected = connected() if callable(connected) else connected
        except Exception:
            connected = None
        return session_id, connected if isinstance(connected, bool) else None

    def _record_retained_failure(self, failed_sample: dict[str, Any]) -> dict[str, Any]:
        """Keep a proven location while making the failed refresh visible."""
        failure = {
            "status": failed_sample.get("status", "unresolved"),
            "reason": failed_sample.get("reason", "runtime player refresh failed"),
        }
        self.last_refresh_failure = failure
        retained = dict(self.latest or {})
        retained["cache"] = {
            "refresh_status": "retained_after_failed_background_refresh",
            "last_failure": failure,
            "session_bound": True,
        }
        self.latest = retained
        return retained

    async def _record_zone_transition(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
        *,
        session_id: str | None,
        previous_session_id: str | None,
    ) -> None:
        """Persist a bounded live transition at the lowest reliable sample boundary.

        RuntimeHub normally observes the same change, but several consumers can
        refresh PlayerRuntime independently while a Field/Mapper chain is being
        rebuilt.  Recording here makes the evidence source independent of which
        cache endpoint happened to win that race.  A session match is required;
        reconnects and savestate/session resets must never become reusable Warp
        evidence.
        """
        if not isinstance(previous, dict) or not isinstance(current, dict):
            return
        if not session_id or previous_session_id != session_id:
            return
        before_zone = previous.get("zone_id")
        after_zone = current.get("zone_id")
        if not isinstance(before_zone, int) or not isinstance(after_zone, int) or before_zone == after_zone:
            return
        before_grid = ((previous.get("position") or {}).get("grid") or {})
        after_grid = ((current.get("position") or {}).get("grid") or {})
        if not all(isinstance(before_grid.get(axis), int) for axis in ("x", "y", "z")):
            return
        if not all(isinstance(after_grid.get(axis), int) for axis in ("x", "y", "z")):
            return
        evidence = WarpTransitionEvidence(
            source_zone=before_zone,
            source_record=None,
            source_grid=(before_grid["x"], before_grid["y"], before_grid["z"]),
            destination_zone=after_zone,
            landing_grid=(after_grid["x"], after_grid["y"], after_grid["z"]),
            frame_before=int(previous.get("frame") or 0),
            frame_after=int(current.get("frame") or 0),
            raw_arg2=None,
            session_id=session_id,
        )
        result = runtime_warp_evidence.record(evidence)
        # This is an observation event, not a promotion of a ROM target Warp
        # index.  Consumers can wait on it without treating the connector as
        # executable before the normal repetition/landing gates are satisfied.
        await agent_event_bus.publish(
            "map.zone.transition.observed",
            frame=int(current.get("frame") or 0),
            session_id=session_id,
            resources={
                "player_runtime": "/api/v1/player/runtime",
                "warp_evidence": "/api/v1/navigation/warp-evidence",
                "game_current": "/api/v1/game/current",
            },
            summary="Live Zone transition observed from consecutive PlayerRuntime samples.",
            data={
                "source_zone": before_zone,
                "source_grid": {axis: before_grid[axis] for axis in ("x", "y", "z")},
                "destination_zone": after_zone,
                "landing_grid": {axis: after_grid[axis] for axis in ("x", "y", "z")},
                "evidence_count": result.get("count"),
                "recorded": result.get("recorded"),
                "target_warp_identity": "unresolved",
            },
        )

    async def sample(self, reader: MemoryReader, *, allow_discovery: bool = False) -> dict[str, Any]:
        """Sample cached player structures without scheduling RAM-wide discovery by default."""
        session_id, connected = self._transport_identity(reader)
        cached_is_usable = bool(self.latest and self.latest.get("status") in {"resolved", "candidate"})

        # A Field pointer cache belongs to one emulator attachment.  Do not
        # carry its coordinates into a reconnect or a known broken transport.
        if connected is False:
            self.invalidate()
            return {
                "format": "black2-runtime-player-live/v3",
                "status": "unresolved",
                "confidence": "unresolved",
                "reason": "BizHawk bridge is disconnected; cached player location was invalidated",
            }
        if cached_is_usable and self.latest_session_id != session_id:
            self.invalidate()
            cached_is_usable = False

        previous = self.last_resolved
        previous_session_id = self.last_resolved_session_id
        sample = await self.locator.sample_player(reader, allow_discovery=allow_discovery)
        if sample.get("status") not in {"resolved", "candidate"}:
            # The high-frequency state engine is intentionally discovery-free.
            # A transient locator miss must not erase a usable location that
            # was established by an explicit discovery in this same attachment.
            if (
                not allow_discovery
                and cached_is_usable
                and session_id is not None
                and self.latest_session_id == session_id
            ):
                return self._record_retained_failure(sample)
            self.latest = sample
            self.latest_session_id = None
            self.last_refresh_failure = None
            return sample
        sample = self._apply_temporal(sample)
        sample["cache"] = {
            "refresh_status": "fresh",
            "last_failure": None,
            "session_bound": session_id is not None,
        }
        self.latest = sample
        self.latest_session_id = session_id
        if previous_session_id == session_id:
            await self._record_zone_transition(
                previous, sample, session_id=session_id,
                previous_session_id=previous_session_id,
            )
        self.last_resolved = dict(sample)
        self.last_resolved_session_id = session_id
        self.last_refresh_failure = None
        return sample


player_runtime_service = PlayerRuntimeService()
