"""Evidence records used to promote raw Warp arguments.

The ROM event record is intentionally lossless and conservative.  This small
value object gives later live-transition tooling a stable place to store the
observations required before ``arg2_raw`` can become a target record index.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import threading
from typing import Any


@dataclass(frozen=True)
class WarpTransitionEvidence:
    source_zone: int
    source_record: int | None
    source_grid: tuple[int, int, int]
    destination_zone: int
    landing_grid: tuple[int, int, int]
    frame_before: int
    frame_after: int
    raw_arg2: int | None
    session_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evidence_matches_raw_arg2(
    evidence: list[WarpTransitionEvidence], *,
    source_zone: int,
    source_record: int,
    raw_arg2: int | None,
) -> bool:
    """Return whether at least two observations agree on the raw argument."""
    matching = [
        item for item in evidence
        if item.source_zone == source_zone
        and item.source_record == source_record
        and item.raw_arg2 == raw_arg2
    ]
    return len(matching) >= 2


class RuntimeWarpEvidenceStore:
    """Persist bounded, read-only observations of live Zone transitions.

    A Zone change is evidence that a transition happened, not proof that a
    ROM ``arg2`` is a target record index.  The connector layer uses this
    store to expose the observed landing and a repetition count while keeping
    target-record promotion disabled until a separate experiment proves it.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[3]
        self.path = Path(root) if root is not None else project_root / "runtime" / "navigation" / "warp_transition_evidence.json"
        self._lock = threading.RLock()
        self._loaded = False
        self._records: list[dict[str, Any]] = []

    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                records = payload.get("records") if isinstance(payload, dict) else []
                self._records = [item for item in records if isinstance(item, dict)][-256:]
            except (OSError, ValueError, TypeError):
                self._records = []
            self._loaded = True

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": "black2-runtime-warp-evidence/v1",
            "policy": "Zone transition and landing are observed; ROM target warp identity remains unresolved.",
            "records": self._records[-256:],
        }
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    @staticmethod
    def _grid_dict(value: Any) -> dict[str, int]:
        """Normalize JSON/dataclass grid forms at the persistence boundary.

        ``dataclasses.asdict`` serializes the tuple fields as JSON arrays,
        while newer event payloads use named ``x/y/z`` objects.  Both are
        valid evidence representations and must remain readable after a
        restart.
        """
        if isinstance(value, dict):
            try:
                if all(isinstance(value.get(axis), int) for axis in ("x", "y", "z")):
                    return {axis: int(value[axis]) for axis in ("x", "y", "z")}
            except (KeyError, TypeError, ValueError):
                return {}
            return {}
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            try:
                return {axis: int(value[index]) for index, axis in enumerate(("x", "y", "z"))}
            except (TypeError, ValueError):
                return {}
        return {}

    def record(self, evidence: WarpTransitionEvidence) -> dict[str, Any]:
        item = evidence.as_dict()
        with self._lock:
            self._load()
            # The hub can sample the same transition for several frames.  A
            # frame/session key makes the record idempotent without hiding a
            # second traversal in the same session.  RuntimeHub and the
            # explicit PlayerRuntime endpoint can also race and observe the
            # same transition with adjacent frame_after values, so use the
            # complete endpoint identity plus a small frame window as a second
            # debounce key.  A real later traversal is hundreds of frames
            # away and remains a separate observation.
            duplicate = any(
                (
                    row.get("session_id") == item.get("session_id")
                    and row.get("frame_before") == item.get("frame_before")
                    and row.get("frame_after") == item.get("frame_after")
                    and row.get("source_zone") == item.get("source_zone")
                    and row.get("destination_zone") == item.get("destination_zone")
                )
                or (
                    row.get("session_id") == item.get("session_id")
                    and row.get("source_zone") == item.get("source_zone")
                    and row.get("destination_zone") == item.get("destination_zone")
                    and row.get("source_grid") == item.get("source_grid")
                    and row.get("landing_grid") == item.get("landing_grid")
                    and abs(int(row.get("frame_after") or 0) - int(item.get("frame_after") or 0)) <= 30
                )
                for row in self._records
            )
            if not duplicate:
                self._records.append(item)
                self._records = self._records[-256:]
                self._save()
            return {"recorded": not duplicate, "evidence": item, "count": len(self._records)}

    def match(
        self,
        *,
        source_zone: int,
        source_x: int | None,
        source_z: int | None,
        destination_zone: int | None,
        landing: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self._load()
        with self._lock:
            matches: list[dict[str, Any]] = []
            for row in self._records:
                if row.get("source_zone") != source_zone or row.get("destination_zone") != destination_zone:
                    continue
                source_grid = self._grid_dict(row.get("source_grid"))
                if source_x is not None and abs(int(source_grid.get("x", 10**9)) - source_x) > 1:
                    continue
                if source_z is not None and abs(int(source_grid.get("z", 10**9)) - source_z) > 1:
                    continue
                if isinstance(landing, dict):
                    observed_landing = self._grid_dict(row.get("landing_grid"))
                    if any(
                        key in landing and landing.get(key) is not None
                        and observed_landing.get(key) != landing.get(key)
                        for key in ("x", "y", "z")
                    ):
                        continue
                normalized = dict(row)
                normalized["source_grid"] = source_grid
                normalized["landing_grid"] = self._grid_dict(row.get("landing_grid"))
                matches.append(normalized)
            return matches

    def snapshot(self) -> dict[str, Any]:
        self._load()
        with self._lock:
            return {
                "format": "black2-runtime-warp-evidence/v1",
                "path": str(self.path),
                "count": len(self._records),
                "records": [dict(item) for item in self._records[-64:]],
                "policy": "Observed source/destination/landing only; target Warp record identity remains unresolved.",
            }


runtime_warp_evidence = RuntimeWarpEvidenceStore()
