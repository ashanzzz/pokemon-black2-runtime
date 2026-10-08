"""Bounded before/after journal for NPC battle probes."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_map(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = payload.get("npcs") if isinstance(payload, dict) else []
    return {
        str(row.get("npc_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("npc_id") is not None
    }


def _compact(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    runtime = row.get("runtime") if isinstance(row.get("runtime"), dict) else {}
    lifecycle = row.get("lifecycle") if isinstance(row.get("lifecycle"), dict) else {}
    return {
        "npc_id": row.get("npc_id"),
        "record_index": row.get("record_index"),
        "zone_id": row.get("zone_id"),
        "coordinate": row.get("coordinate"),
        "script_id": row.get("script_id"),
        "flag_id": row.get("flag_id"),
        "capability": row.get("capability"),
        "runtime": {
            "bound": runtime.get("bound"),
            "present": runtime.get("present"),
            "actor_uid": runtime.get("actor_uid"),
            "slot": runtime.get("slot"),
            "address": runtime.get("address"),
            "grid": runtime.get("grid"),
            "facing": runtime.get("facing"),
            "raw": runtime.get("raw") or {},
        },
        "lifecycle": lifecycle,
        "battle_observation": row.get("battle_observation"),
        "evidence": row.get("evidence"),
    }


def _compact_status(payload: dict[str, Any] | None, npc_ids: set[str] | None = None) -> dict[str, Any]:
    """Keep journal entries bounded and focused on the probed NPCs."""
    if not isinstance(payload, dict):
        return {"format": "black2-npc-battle-status/v1", "npcs": []}
    rows = _row_map(payload)
    selected = [rows[key] for key in sorted(rows) if npc_ids is None or key in npc_ids]
    return {
        "format": payload.get("format", "black2-npc-battle-status/v1"),
        "zone_id": payload.get("zone_id"),
        "frame": payload.get("frame"),
        "npcs": [_compact(row) for row in selected],
        "policy": payload.get("policy"),
    }


def _item_npc_ids(item: dict[str, Any]) -> set[str]:
    """Resolve the NPC ids covered by one journal item.

    Older records may not have stored ``npc_ids`` explicitly, so fall back to
    the compact before/after rows.  This keeps the read API useful across
    journal versions without reopening the large raw status payload.
    """
    explicit = item.get("npc_ids")
    if isinstance(explicit, list):
        values = {str(value) for value in explicit if value is not None}
        if values:
            return values
    values: set[str] = set()
    for side in (item.get("before"), item.get("after")):
        rows = side.get("npcs") if isinstance(side, dict) else None
        if not isinstance(rows, list):
            continue
        values.update(
            str(row.get("npc_id"))
            for row in rows
            if isinstance(row, dict) and row.get("npc_id") is not None
        )
    return values


def _dialogue_summary(value: dict[str, Any] | None) -> dict[str, Any]:
    """Return bounded dialogue evidence for the per-NPC status projection."""
    if not isinstance(value, dict):
        return {"active": None, "text": "", "speaker": None}
    text = value.get("full_text") or value.get("text") or ""
    return {
        "active": value.get("active") if isinstance(value.get("active"), bool) else None,
        "text": str(text)[:1000],
        "speaker": value.get("speaker"),
        "speaker_category": value.get("speaker_category"),
    }


def _probe_meaning(outcome: str | None) -> str:
    meanings = {
        "dialogue_observed_without_battle": (
            "本次交互观察到对话且前后未进入战斗；不等于该 NPC 永远没有战斗。"
        ),
        "battle_active_after_probe": (
            "本次交互结束时战斗仍处于 active；已建立交互到战斗的候选因果链，训练家身份和胜负仍需单独验证。"
        ),
        "no_battle_transition_observed": (
            "本次交互前后没有观察到战斗转换；可能是普通交互、目标未触发或探测未覆盖完整脚本。"
        ),
        "precondition_modal_active": (
            "探测开始前已经存在对话或其他模态层；本次没有完成 NPC 交互，不能用于判断该 NPC 是否有战斗。"
        ),
        "precondition_battle_active": (
            "探测开始前已经处于战斗；本次没有完成 NPC 交互，不能用于判断该 NPC 是否有战斗。"
        ),
        "probe_failed": "导航/交互探测未完成；本次证据不能用于判断该 NPC 是否有战斗。",
        "read_only_observation": "本次只读取状态，没有执行 NPC 交互；不能据此判断该 NPC 是否有战斗。",
        "unresolved": "探测证据不足，不能判断该 NPC 是否有战斗。",
    }
    return meanings.get(str(outcome), meanings["unresolved"])


def diff_status(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """Return a bounded field-level diff for matching NPC rows."""
    left, right = _row_map(before), _row_map(after)
    changes: list[dict[str, Any]] = []
    for npc_id in sorted(set(left) | set(right)):
        before_row, after_row = _compact(left.get(npc_id)), _compact(right.get(npc_id))
        if before_row == after_row:
            continue
        before_runtime = (before_row or {}).get("runtime") or {}
        after_runtime = (after_row or {}).get("runtime") or {}
        before_raw = before_runtime.get("raw") or {}
        after_raw = after_runtime.get("raw") or {}
        raw_changes = {
            key: {"before": before_raw.get(key), "after": after_raw.get(key)}
            for key in sorted(set(before_raw) | set(after_raw))
            if before_raw.get(key) != after_raw.get(key)
        }
        changes.append({
            "npc_id": npc_id,
            "presence_changed": before_runtime.get("present") != after_runtime.get("present"),
            "binding_changed": before_runtime.get("bound") != after_runtime.get("bound"),
            "raw_changes": raw_changes,
            "defeat_status_before": ((before_row or {}).get("lifecycle") or {}).get("defeat_status"),
            "defeat_status_after": ((after_row or {}).get("lifecycle") or {}).get("defeat_status"),
            "before": before_row,
            "after": after_row,
        })
    return {
        "status": "changed" if changes else "unchanged",
        "changed_count": len(changes),
        "changes": changes,
        "policy": "A raw actor diff is evidence for investigation; it is not a victory claim without a verified event-flag or battle result.",
    }


class NpcBattleHistory:
    def __init__(self, path: str | Path | None = None, *, max_entries: int = 200) -> None:
        root = Path(__file__).resolve().parents[3] / "runtime" / "ai_context"
        self.path = Path(path) if path is not None else root / "npc_battle_observations.ndjson"
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()

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

    def record(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        operation: str = "npc_battle_probe",
        session_id: str | None = None,
        battle_before: dict[str, Any] | None = None,
        battle_after: dict[str, Any] | None = None,
        dialogue_before: dict[str, Any] | None = None,
        dialogue_after: dict[str, Any] | None = None,
        note: str | None = None,
        npc_ids: list[str] | None = None,
        probe_status: str | None = None,
        probe_failure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_ids = {str(value) for value in (npc_ids or []) if value is not None}
        before_compact = _compact_status(before, selected_ids or None)
        after_compact = _compact_status(after, selected_ids or None)
        before_active = battle_before.get("active") if isinstance(battle_before, dict) else None
        after_active = battle_after.get("active") if isinstance(battle_after, dict) else None
        after_current = battle_after if isinstance(battle_after, dict) else {}
        after_dialogue = dialogue_after if isinstance(dialogue_after, dict) else (
            after_current.get("overlays", {}).get("dialogue") if isinstance(after_current.get("overlays"), dict) else {}
        )
        before_dialogue_active = dialogue_before.get("active") is True if isinstance(dialogue_before, dict) else False
        before_battle_active = before_active is True
        normalized_probe_status = str(probe_status or "completed")
        if before_dialogue_active:
            probe_outcome = "precondition_modal_active"
        elif before_battle_active:
            # A battle already active before the NPC operation is a precondition,
            # not evidence about this NPC.  Keep it separate from a battle that
            # started as a result of the probe.
            probe_outcome = "precondition_battle_active"
        elif after_active is True:
            # The emulator/automation layer may report a failed or stopped
            # operation immediately after the battle transition.  The positive
            # battle observation is stronger than that transport-level status.
            probe_outcome = "battle_active_after_probe"
        elif normalized_probe_status in {"failed", "start_failed", "cancelled", "stopped", "timeout"}:
            probe_outcome = "probe_failed"
        elif normalized_probe_status == "read_only":
            probe_outcome = "read_only_observation"
        elif before_active is False and after_active is False and isinstance(after_dialogue, dict) and after_dialogue.get("active") is True:
            probe_outcome = "dialogue_observed_without_battle"
        elif before_active is False and after_active is False:
            probe_outcome = "no_battle_transition_observed"
        else:
            probe_outcome = "unresolved"
        item = {
            "format": "black2-npc-battle-observation/v1",
            "observed_at": _now(),
            "operation": operation,
            "session_id": session_id,
            "note": note,
            "npc_ids": sorted(selected_ids) if selected_ids else None,
            "probe_status": normalized_probe_status,
            "probe_failure": probe_failure,
            "before": before_compact,
            "after": after_compact,
            "battle_before": battle_before,
            "battle_after": battle_after,
            "dialogue_before": dialogue_before,
            "dialogue_after": dialogue_after,
            "probe_outcome": probe_outcome,
            "battle_transition": {
                "before_active": before_active,
                "after_active": after_active,
                "started": before_active is False and after_active is True,
                "ended": before_active is True and after_active is False,
            },
            "diff": diff_status(before_compact, after_compact),
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
            rows = self._read_locked()
            if len(rows) > self.max_entries:
                self.path.write_text(
                    "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows[-self.max_entries:]),
                    encoding="utf-8",
                )
        return {"recorded": True, "record": item}

    def recent(self, limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(int(limit), self.max_entries))
        with self._lock:
            rows = self._read_locked()
        return {
            "format": "black2-npc-battle-observation-history/v1",
            "status": "ok",
            "path": str(self.path),
            "count": len(rows[-limit:]),
            "total_count": len(rows),
            "observations": rows[-limit:],
            "policy": "Before/after actor RAM and battle state are retained; unresolved event flags remain unresolved.",
        }

    def latest_by_npc(self) -> dict[str, dict[str, Any]]:
        """Project the latest bounded probe evidence for each NPC.

        The full journal remains available from ``recent()``.  This compact
        index is intended for the live map endpoint: it lets an agent choose
        the next unprobed candidate without transferring every before/after
        actor row on every poll.
        """
        with self._lock:
            rows = self._read_locked()
        counts: dict[str, int] = {}
        for item in rows:
            for npc_id in _item_npc_ids(item):
                counts[npc_id] = counts.get(npc_id, 0) + 1

        latest: dict[str, dict[str, Any]] = {}
        for item in reversed(rows):
            npc_ids = _item_npc_ids(item)
            if not npc_ids:
                continue
            outcome = item.get("probe_outcome")
            diff = item.get("diff") if isinstance(item.get("diff"), dict) else {}
            changes = diff.get("changes") if isinstance(diff.get("changes"), list) else []
            change_by_id = {
                str(change.get("npc_id")): change
                for change in changes
                if isinstance(change, dict) and change.get("npc_id") is not None
            }
            battle_before = item.get("battle_before") if isinstance(item.get("battle_before"), dict) else {}
            battle_after = item.get("battle_after") if isinstance(item.get("battle_after"), dict) else {}
            dialogue_before = _dialogue_summary(item.get("dialogue_before"))
            dialogue_after = _dialogue_summary(item.get("dialogue_after"))
            before = item.get("before") if isinstance(item.get("before"), dict) else {}
            after = item.get("after") if isinstance(item.get("after"), dict) else {}
            summary = {
                "observed_at": item.get("observed_at"),
                "operation": item.get("operation"),
                "note": item.get("note"),
                "probe_status": item.get("probe_status"),
                "probe_failure": item.get("probe_failure"),
                "probe_count": 0,
                "probe_outcome": outcome,
                "meaning": _probe_meaning(outcome),
                "battle_transition": item.get("battle_transition"),
                "battle": {
                    "before_active": battle_before.get("active"),
                    "after_active": battle_after.get("active"),
                },
                "dialogue": {
                    "before": dialogue_before,
                    "after": dialogue_after,
                },
                "frames": {
                    "before": before.get("frame"),
                    "after": after.get("frame"),
                },
                "diff": {
                    "status": diff.get("status"),
                    "changed_count": diff.get("changed_count", 0),
                    "presence_changed": None,
                    "binding_changed": None,
                    "raw_changes": {},
                },
            }
            for npc_id in npc_ids:
                if npc_id in latest:
                    continue
                change = change_by_id.get(npc_id) or {}
                probe = {
                    **summary,
                    "probe_count": counts.get(npc_id, 1),
                    "diff": {
                        **summary["diff"],
                        "presence_changed": change.get("presence_changed"),
                        "binding_changed": change.get("binding_changed"),
                        "raw_changes": change.get("raw_changes") if isinstance(change.get("raw_changes"), dict) else {},
                    },
                }
                latest[npc_id] = probe
        return latest


npc_battle_history = NpcBattleHistory()


__all__ = ["NpcBattleHistory", "diff_status", "npc_battle_history"]
