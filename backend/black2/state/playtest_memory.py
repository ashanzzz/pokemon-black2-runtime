"""Persistent, evidence-labelled memory for the Black 2 playtest agent.

The game itself remains the authority for runtime state.  This store only
keeps bounded operator memory and never upgrades an unresolved API field into
an empty party, empty bag, or completed story milestone.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4


class PlaytestMemoryStore:
    """Keep long-, medium-, and short-term playtest memory in small files."""

    def __init__(self, root: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[3]
        self.root = Path(root) if root is not None else project_root / "runtime" / "ai_context"
        self.state_path = self.root / "memory_state.json"
        self.long_path = self.root / "long_term.md"
        self.short_path = self.root / "short_term.ndjson"
        self.registry_path = self.root / "memory_registry.json"
        self._lock = threading.RLock()
        self._ensure_files()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _default(self) -> dict[str, Any]:
        return {
            "format": "black2-playtest-memory/v1",
            "updated_at": self._now(),
            "long_term": {
                "story_progress": {
                    "current": "尚未从游戏事件/旗标解码主线节点",
                    "target": "完成当前存档的主线并通关",
                    "status": "unresolved",
                    "evidence": [],
                },
                "verified_facts": [],
                "safety_rules": [
                    "动作前先读直接 PlayerRuntime 与语义状态",
                    "动作后逐步复核位置、Zone、对话、战斗和切图",
                    "party/items 未解码时不得把空数组当作没有资源",
                    "静态寻路只提供 candidate，执行必须由实时落点验证",
                ],
            },
            "medium_term": {
                "current_goal": {
                    "title": "读取当前剧情状态并找到下一条已证实剧情节点",
                    "status": "active",
                    "target": None,
                    "reason": "主线 objective API 尚未给出已验证事件",
                    "evidence": [],
                },
                "current_state": {
                    "location": None,
                    "party": {"status": "unresolved", "count": None, "slots": []},
                    "inventory": {"status": "unresolved", "items": []},
                    "badges": {"status": "unresolved", "value": None},
                },
                "next_action": "先读 /api/v1/ai/context、/api/v1/game/party、/api/v1/game/inventory，再决定剧情动作",
            },
            "short_term": {"recent": []},
            "files": {
                "long_term": str(self.long_path),
                "short_term": str(self.short_path),
                "registry": str(self.registry_path),
            },
        }

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        # The desktop runtime can briefly have an old and replacement backend
        # alive during a hot restart.  A shared ``*.tmp`` name lets those
        # processes overwrite/remove one another's temp file; on Windows the
        # losing replace then surfaces as WinError 5 and can fail an otherwise
        # completed story task.  Keep each atomic write isolated by process,
        # thread and operation.
        temp = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.tmp"
        )
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        temp.write_text(payload, encoding="utf-8")
        last_error: PermissionError | None = None
        try:
            # A hot-restarted desktop backend and a file watcher can briefly
            # hold the destination open on Windows.  Retry the atomic swap
            # instead of turning a completed in-game interaction into an
            # AUTOMATION_INTERNAL failure.
            for attempt in range(12):
                try:
                    temp.replace(path)
                    return
                except PermissionError as error:
                    last_error = error
                    if attempt == 11:
                        break
                    time.sleep(0.025 * (attempt + 1))

            # Best-effort direct fallback.  It is intentionally bounded and
            # only reached after atomic replacement was blocked; normal writes
            # remain atomic.  If the destination is still locked, preserve the
            # complete payload as a pending sidecar so the next memory read
            # can recover it rather than losing the event.
            for attempt in range(4):
                try:
                    path.write_text(payload, encoding="utf-8")
                    return
                except PermissionError as error:
                    last_error = error
                    if attempt < 3:
                        time.sleep(0.05 * (attempt + 1))
            pending = path.with_name(
                f"{path.name}.pending.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.json"
            )
            try:
                pending.write_text(payload, encoding="utf-8")
            except OSError:
                # Memory persistence is auxiliary to the live game action;
                # leave the authoritative event log/runtime evidence usable.
                pass
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _ensure_files(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if not self.state_path.exists():
                self._write_json(self.state_path, self._default())
            if not self.long_path.exists():
                self.long_path.write_text(
                    "# Black 2 Playtest Long-Term Memory\n\n"
                    "主线、已验证事实和安全规则由 `memory_state.json` 同步生成；\n"
                    "未解码字段保持 unresolved，不以空数组代表没有资源。\n",
                    encoding="utf-8",
                )
            if not self.short_path.exists():
                self.short_path.write_text("", encoding="utf-8")
            if not self.registry_path.exists():
                self._write_json(self.registry_path, {
                    "format": "black2-memory-registry/v1",
                    "fields": {
                        "story_progress": {"status": "unresolved", "source": "runtime/API evidence"},
                        "party": {"status": "unresolved", "source": "/api/v1/game/party"},
                        "inventory": {"status": "unresolved", "source": "/api/v1/game/inventory"},
                        "short_term_dialogue": {"status": "candidate", "source": "/api/dialogue/history"},
                    },
                })

    def _load(self) -> dict[str, Any]:
        self._ensure_files()
        pending_files = sorted(
            self.root.glob(f"{self.state_path.name}.pending.*.json"),
            key=lambda item: item.stat().st_mtime_ns if item.exists() else 0,
        )
        if pending_files:
            pending = pending_files[-1]
            try:
                value = json.loads(pending.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    try:
                        self._write_json(self.state_path, value)
                        pending.unlink(missing_ok=True)
                    except OSError:
                        pass
                    return value
            except (OSError, ValueError):
                pass
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = self._default()
        return value if isinstance(value, dict) else self._default()

    def _mirror_long_term(self, state: dict[str, Any]) -> None:
        long_term = state.get("long_term") or {}
        story = long_term.get("story_progress") or {}
        lines = [
            "# Black 2 Playtest Long-Term Memory",
            "",
            f"- 当前剧情：{story.get('current')}（{story.get('status')}）",
            f"- 最终目标：{story.get('target')}",
            "",
            "## 已验证事实",
        ]
        facts = long_term.get("verified_facts") or []
        lines.extend(f"- {item}" for item in facts[-50:])
        lines.extend(["", "## 安全规则"])
        lines.extend(f"- {item}" for item in (long_term.get("safety_rules") or []))
        self.long_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._load()

    def sync_runtime(
        self,
        snapshot: dict[str, Any],
        *,
        objective: dict[str, Any] | None = None,
        note: str | None = None,
        party: dict[str, Any] | None = None,
        inventory: dict[str, Any] | None = None,
        story_progress: dict[str, Any] | None = None,
        player: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a bounded state sample without inventing party/bag facts."""
        with self._lock:
            state = self._load()
            player_data = player if isinstance(player, dict) else (
                snapshot.get("player") if isinstance(snapshot.get("player"), dict) else {}
            )
            # RuntimeHub deliberately does not force a RAM discovery probe on
            # every background poll.  Reuse the shared direct PlayerRuntime
            # sample when the caller has already established it through the
            # explicit live endpoint.
            if player_data.get("status") not in {"resolved", "candidate"}:
                try:
                    from .runtime_player_state import player_runtime_service
                    live = player_runtime_service.latest
                    if isinstance(live, dict):
                        player_data = live
                except Exception:
                    pass
            position = player_data.get("position") if isinstance(player_data.get("position"), dict) else {}
            profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
            party_data = party if isinstance(party, dict) else (
                snapshot.get("party") if isinstance(snapshot.get("party"), dict) else {}
            )
            inventory_data = inventory if isinstance(inventory, dict) else (
                snapshot.get("inventory") if isinstance(snapshot.get("inventory"), dict) else {}
            )
            state["medium_term"]["current_state"] = {
                "location": {
                    "zone_id": player_data.get("zone_id"),
                    "grid": position.get("grid"),
                    "confidence": player_data.get("confidence", "unresolved"),
                },
                "party": {
                    "status": party_data.get("status", "unresolved"),
                    "count": party_data.get("count"),
                    "slots": party_data.get("slots") or [],
                },
                "inventory": {
                    "status": inventory_data.get("status", "unresolved"),
                    "items": inventory_data.get("items") or [],
                },
                "badges": {"status": "candidate" if profile.get("badges") is not None else "unresolved", "value": profile.get("badges")},
            }
            if isinstance(story_progress, dict):
                state["long_term"]["story_progress"] = story_progress
            if objective and isinstance(objective, dict):
                state["medium_term"]["current_goal"] = objective
            if note:
                state["medium_term"]["next_action"] = note
            state["updated_at"] = self._now()
            self._write_json(self.state_path, state)
            self._mirror_long_term(state)
            return state

    def record_event(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self._load()
            item = {"timestamp": self._now(), **event}
            recent = state.setdefault("short_term", {}).setdefault("recent", [])
            recent.append(item)
            state["short_term"]["recent"] = recent[-50:]
            state["updated_at"] = self._now()
            self._write_json(self.state_path, state)
            with self.short_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
            return item


playtest_memory = PlaytestMemoryStore()
