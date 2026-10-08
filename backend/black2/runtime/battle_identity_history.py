"""Persist bounded battle-identity observations for the playtest agent.

The live battle decoder is deliberately conservative: it may return a RAM
species candidate without proving the battle kind, and it may return a ROM
trainer candidate without proving the triggering NPC.  This module stores the
observation as-is so a later API call can answer "what was the last encounter?"
without promoting an unresolved field to a fact.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _species_ids(side: Any) -> list[int]:
    if not isinstance(side, dict):
        return []
    rows = side.get("party") if isinstance(side.get("party"), list) else []
    result: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get("species_id")
        if isinstance(value, int):
            result.add(value)
    active = side.get("active")
    if isinstance(active, dict) and isinstance(active.get("species_id"), int):
        result.add(active["species_id"])
    return sorted(result)


class BattleIdentityHistory:
    """A small append-only NDJSON journal with bounded readback."""

    def __init__(self, path: str | Path | None = None, *, max_entries: int = 200) -> None:
        root = Path(__file__).resolve().parents[3] / "runtime" / "ai_context"
        self.path = Path(path) if path is not None else root / "battle_identity_history.ndjson"
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()
        self._last_signature: tuple[Any, ...] | None = None

    @staticmethod
    def _compact(identity: dict[str, Any]) -> dict[str, Any]:
        kind = identity.get("battle_kind") if isinstance(identity.get("battle_kind"), dict) else {}
        trainer = identity.get("trainer") if isinstance(identity.get("trainer"), dict) else {}
        player = identity.get("player") if isinstance(identity.get("player"), dict) else {}
        opponent = identity.get("opponent") if isinstance(identity.get("opponent"), dict) else {}
        return {
            "status": identity.get("status"),
            "verified": identity.get("verified"),
            "battle_kind": kind,
            "trainer": trainer,
            "player": {
                "status": player.get("status"),
                "active": player.get("active"),
                "party": player.get("party", []),
            },
            "opponent": {
                "status": opponent.get("status"),
                "active": opponent.get("active"),
                "party": opponent.get("party", []),
            },
            "trainer_text": identity.get("trainer_text"),
            "causal_context": identity.get("causal_context"),
            "zone_script_catalog": identity.get("zone_script_catalog"),
            "zone_trainer_candidates": identity.get("zone_trainer_candidates", []),
            "reason": identity.get("reason"),
            "limitations": identity.get("limitations", []),
        }

    @staticmethod
    def _signature(
        identity: dict[str, Any],
        *,
        session_id: str | None,
        zone_id: int | None,
    ) -> tuple[Any, ...]:
        kind = identity.get("battle_kind") if isinstance(identity.get("battle_kind"), dict) else {}
        trainer = identity.get("trainer") if isinstance(identity.get("trainer"), dict) else {}
        opponent = identity.get("opponent") if isinstance(identity.get("opponent"), dict) else {}
        return (
            session_id,
            zone_id,
            kind.get("value"),
            trainer.get("trainer_id"),
            tuple(_species_ids(opponent)),
        )

    def record(
        self,
        identity: dict[str, Any],
        *,
        presence: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        session_id: str | None = None,
        source: str = "runtime_hub:battle_transition",
    ) -> dict[str, Any]:
        presence = presence if isinstance(presence, dict) else {}
        context = context if isinstance(context, dict) else {}
        overworld = context.get("battle_overworld") if isinstance(context.get("battle_overworld"), dict) else {}
        zone_value = context.get("zone_id")
        if not isinstance(zone_value, int):
            zone_value = overworld.get("zone_id") if isinstance(overworld.get("zone_id"), int) else None
        signature = self._signature(identity, session_id=session_id, zone_id=zone_value)
        with self._lock:
            if self._last_signature == signature:
                return {"recorded": False, "deduplicated": True, "signature": list(signature)}
            record = {
                "format": "black2-battle-identity-observation/v1",
                "observed_at": _now(),
                "source": source,
                "session_id": session_id,
                "frame": presence.get("frame"),
                "zone_id": zone_value,
                "overworld_context": overworld or context,
                "identity": self._compact(identity),
                "presence_evidence": {
                    "active": presence.get("active"),
                    "active_status": presence.get("active_status"),
                    "field_busy": presence.get("field_busy"),
                    "pointers": presence.get("pointers"),
                    "frame": presence.get("frame"),
                },
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._last_signature = signature
            self._trim_locked()
            return {"recorded": True, "deduplicated": False, "signature": list(signature), "record": record}

    def _read_locked(self) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines[-self.max_entries:]:
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def _trim_locked(self) -> None:
        rows = self._read_locked()
        if len(rows) <= self.max_entries:
            return
        try:
            self.path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows[-self.max_entries:]),
                encoding="utf-8",
            )
        except OSError:
            pass

    def recent(self, limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(int(limit), self.max_entries))
        with self._lock:
            rows = self._read_locked()
        return {
            "format": "black2-battle-identity-history/v1",
            "status": "ok",
            "path": str(self.path),
            "count": len(rows[-limit:]),
            "total_count": len(rows),
            "observations": rows[-limit:],
            "policy": "Observations preserve candidate/unresolved labels; they do not prove live trainer causality.",
        }


battle_identity_history = BattleIdentityHistory()


__all__ = ["BattleIdentityHistory", "battle_identity_history"]
