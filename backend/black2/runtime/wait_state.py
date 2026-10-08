"""Evidence-gated wait/decision projection for Pokémon Agent consumers.

The runtime samples continuously, but an Agent should not have to reason on
every emulator frame.  This module turns the cached semantic snapshot into a
small, stable boundary contract:

* ``auto_transition`` is a system-owned transition such as a dialogue page
  that is ready for one A edge;
* ``decision`` is an Agent-owned choice such as a dialogue choice, battle
  command, or menu selection;
* ``hold`` means the runtime is not decoded well enough to authorize input.

The projection is deliberately pure.  It never reads RAM and never sends
input.  A ``boundary_key`` excludes frame counters so the hub can emit one
event per semantic boundary instead of one event per sample.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .layered_status import normalize_screen_type


WAIT_FORMAT = "black2-wait-state/v1"


# The live TextPrinter/Window binding is still unresolved for a small class
# of field-dialogue allocations.  Do not turn that uncertainty into a global
# "press A" rule: a hidden yes/no choice would be an unsafe default.  This
# registry contains only streams whose page edges were explicitly observed
# with the bridge-owned input API and a semantic screenshot check.  Unknown
# streams remain on ``wait_for_state_change`` until their renderer contract is
# decoded.
_VERIFIED_AUTO_ADVANCE_STREAMS: dict[str, dict[str, Any]] = {
    # EXP_012 / 2026-09-09: Zone446 gate NPC0, A1 -> page 2, A2 -> page 3,
    # A3 -> OVERWORLD; no choice menu appeared on any page.
    "c25534c700d733986ea9596f3fa17a10e6ffde3addbb589fba3af47ae8801be0": {
        "contract_id": "EXP_012_zone446_gate_no_badge_dialogue",
        "page_edges_observed": 3,
        "max_auto_edges": 5,
        "minimum_edge_interval_seconds": 1.10,
        "source": "bridge A-edge sequence + semantic capture",
    },
    # EXP_020 / 2026-09-09: Zone439 ranch story NPC (script 11/model 28).
    # The live RAM stream was loaded while the renderer pointer was unresolved;
    # a semantic screenshot showed the first page was visibly waiting for A,
    # and the prior case184 replay observed four A edges through OVERWORLD.
    # Keep this content-addressed so an unrelated unresolved dialogue stream
    # remains frozen rather than inheriting the permission.
    "404db37d993dc99a7bd7166ddf611a52b588ed3fc6be98e60f0e5416f81cc147": {
        "contract_id": "EXP_020_zone439_ranch_story_npc",
        "page_edges_observed": 4,
        "max_auto_edges": 6,
        "minimum_edge_interval_seconds": 1.10,
        "source": "live ActorSystem script11/model28 + RAM loaded text + semantic screenshot + case184 replay",
    },
    # EXP_025 / 2026-10-02: Zone443 Pokemon Center Nurse post-recovery page 1.
    "ad2430a9f377575c3d0000d1a469138c4084bf81f49ac8a1984eb16f36691ba6": {
        "contract_id": "EXP_025_zone443_nurse_recovery_done",
        "page_edges_observed": 1,
        "max_auto_edges": 2,
        "minimum_edge_interval_seconds": 1.10,
        "source": "live ActorSystem script2100 + RAM loaded text post-chime",
    },
    # EXP_025 / 2026-10-02: Zone443 Pokemon Center Nurse farewell.
    "ad59abed7e1bc3e01fbcf058d0953b39c9a6abc036c2ddadd6d540c760e9445a": {
        "contract_id": "EXP_025_zone443_nurse_farewell",
        "page_edges_observed": 1,
        "max_auto_edges": 2,
        "minimum_edge_interval_seconds": 1.10,
        "source": "live ActorSystem script2100 + RAM loaded text terminal page",
    },

}


def _context(snapshot: dict[str, Any]) -> dict[str, Any]:
    semantic = snapshot.get("semantic") if isinstance(snapshot.get("semantic"), dict) else {}
    value = semantic.get("context") if isinstance(semantic.get("context"), dict) else {}
    return value


def _semantic(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = snapshot.get("semantic") if isinstance(snapshot.get("semantic"), dict) else {}
    return value


def _runtime_ready(snapshot: dict[str, Any]) -> bool:
    transport = snapshot.get("transport") if isinstance(snapshot.get("transport"), dict) else {}
    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
    return bool(
        transport.get("bridge_connected") is True
        and runtime.get("semantic_status") == "ready"
    )


def _choices(context: dict[str, Any]) -> list[Any]:
    value = context.get("choices")
    return value if isinstance(value, list) else []


def _battle_ui(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    value = snapshot.get("battle_ui")
    return value if isinstance(value, dict) else None


def _battle(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = snapshot.get("battle")
    return value if isinstance(value, dict) else {}


def _compact_text(value: Any, limit: int = 500) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:limit]


def _verified_auto_advance_contract(loaded_text: str | None) -> dict[str, Any] | None:
    """Return a narrow evidence contract for a previously tested stream.

    This is deliberately content-addressed.  A new script, even in the same
    zone, cannot inherit the permission merely because it is a dialogue.
    ``loaded_text`` is script-stream evidence, not a claim that the visible
    page has been decoded.
    """
    if not isinstance(loaded_text, str) or not loaded_text:
        return None
    normalized = loaded_text.replace("\r\n", "\n").strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    contract = _VERIFIED_AUTO_ADVANCE_STREAMS.get(digest)
    if not contract:
        return None
    return {**contract, "loaded_text_sha256": digest}


def _boundary_key(parts: Any) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
    return f"wait_{digest}"


def _base(
    *,
    status: str,
    kind: str,
    reason: str,
    decision_required: bool | None,
    allowed_actions: list[str],
    actions: list[dict[str, Any]],
    auto_policy: str,
    key_parts: Any,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "format": WAIT_FORMAT,
        "status": status,
        "kind": kind,
        "reason": reason,
        "decision_required": decision_required,
        "allowed_actions": allowed_actions,
        "actions": actions,
        "auto_policy": auto_policy,
        "boundary_key": _boundary_key(key_parts),
        "context": context or {},
    }
    # ``wait_id`` is a stable precondition token for an action request.  It is
    # intentionally derived from the boundary, not from a wall-clock value.
    payload["wait_id"] = payload["boundary_key"]
    return payload


def _unresolved(reason: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
    return _base(
        status="unresolved",
        kind="hold",
        reason=reason,
        decision_required=None,
        allowed_actions=[],
        actions=[],
        auto_policy="hold",
        key_parts=["unresolved", reason],
        context=context,
    )


def _dialogue_wait(snapshot: dict[str, Any], context: dict[str, Any], semantic: dict[str, Any]) -> dict[str, Any]:
    choices = _choices(context)
    text = _compact_text(context.get("dialogue_text"))
    loaded = _compact_text(context.get("loaded_dialogue_text"))
    full = _compact_text(context.get("full_dialogue_text"))
    pointer = context.get("active_pointer")
    dialogue_context = {
        "screen_type": normalize_screen_type(context.get("screen_type")),
        "speaker": context.get("speaker"),
        "speaker_category": context.get("speaker_category"),
        "text": text,
        "loaded_text": loaded,
        "full_text": full,
        "active_pointer": pointer,
        "choices": choices,
    }
    if choices:
        return _base(
            status="waiting",
            kind="decision",
            reason="dialogue_choice",
            decision_required=True,
            allowed_actions=["dialogue.choose"],
            actions=[{"type": "dialogue.choose", "endpoint": "/api/actions/dialogue/choice", "choices": choices}],
            auto_policy="pause",
            key_parts=["dialogue_choice", pointer, choices],
            context=dialogue_context,
        )

    ready = semantic.get("ready_for_input") is True
    calibrated_contract = _verified_auto_advance_contract(loaded)
    if calibrated_contract is not None:
        dialogue_context["auto_advance_contract"] = calibrated_contract
    if ready or calibrated_contract is not None:
        calibrated = calibrated_contract is not None and not ready
        return _base(
            status="waiting",
            kind="auto_transition",
            reason="dialogue_page_calibrated" if calibrated else "dialogue_page",
            decision_required=False,
            allowed_actions=["dialogue.advance"],
            actions=[{
                "type": "dialogue.advance",
                "button": "A",
                "endpoint": "/api/v1/agent/wait/advance",
                "policy": "press_once_then_wait_for_next_event",
                **({"evidence": calibrated_contract} if calibrated else {}),
            }],
            auto_policy="press_A_once",
            key_parts=["dialogue_page", pointer, full or loaded or text, calibrated_contract and calibrated_contract.get("contract_id")],
            context=dialogue_context,
        )

    return _base(
        status="waiting",
        kind="auto_transition",
        reason="dialogue_text_printing",
        decision_required=False,
        allowed_actions=[],
        actions=[],
        auto_policy="wait_for_state_change",
        key_parts=["dialogue_text_printing", pointer, full or loaded or text],
        context=dialogue_context,
    )


def _battle_wait(snapshot: dict[str, Any], battle: dict[str, Any]) -> dict[str, Any]:
    ui = _battle_ui(snapshot)
    field_busy = battle.get("field_busy") if isinstance(battle.get("field_busy"), dict) else {}
    if field_busy.get("raw") == 2:
        return _base(
            status="waiting",
            kind="auto_transition",
            reason="battle_transition",
            decision_required=False,
            allowed_actions=[],
            actions=[],
            auto_policy="wait_for_state_change",
            key_parts=["battle_transition", field_busy.get("raw")],
            context={"field_busy": field_busy},
        )

    phase = ((ui or {}).get("phase") or {}).get("value")
    phase_raw = ((ui or {}).get("phase") or {}).get("raw_u32")
    cursor = (ui or {}).get("cursor") or {}
    if phase == "command_menu":
        actions = [
            {"type": "battle.fight", "endpoint": "/api/v1/battle/ui-actions"},
            {"type": "battle.switch", "endpoint": "/api/v1/battle/decisions"},
            {"type": "battle.item", "endpoint": "/api/v1/battle/decisions"},
            {"type": "battle.run", "endpoint": "/api/v1/battle/decisions"},
        ]
        return _base(
            status="waiting",
            kind="decision",
            reason="battle_command_choice",
            decision_required=True,
            allowed_actions=[item["type"] for item in actions],
            actions=actions,
            auto_policy="pause",
            key_parts=["battle_command_choice", phase_raw],
            context={"phase": phase, "phase_raw": phase_raw, "cursor": cursor},
        )
    if phase == "move_menu":
        slot = cursor.get("slot")
        actions = [
            {"type": "battle.use_move", "move_slot": move_slot, "endpoint": "/api/v1/battle/ui-actions"}
            for move_slot in (1, 2, 3)
        ]
        return _base(
            status="waiting",
            kind="decision",
            reason="battle_move_choice",
            decision_required=True,
            allowed_actions=[item["type"] for item in actions],
            actions=actions,
            auto_policy="pause",
            key_parts=["battle_move_choice", phase_raw, cursor.get("raw_u32"), slot],
            context={"phase": phase, "phase_raw": phase_raw, "cursor": cursor},
        )

    return _unresolved(
        "battle_phase_unresolved",
        context={
            "battle_active": battle.get("active"),
            "field_busy": field_busy,
            "battle_ui": ui,
        },
    )


def derive_wait_state(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Derive the current wait boundary from a cached RuntimeHub snapshot."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    context = _context(snapshot)
    semantic = _semantic(snapshot)
    battle = _battle(snapshot)
    transport = snapshot.get("transport") if isinstance(snapshot.get("transport"), dict) else {}
    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}

    if transport.get("bridge_connected") is not True:
        state = _unresolved("bridge_disconnected")
    elif not _runtime_ready(snapshot):
        state = _unresolved("semantic_state_unresolved")
    elif isinstance(battle.get("field_busy"), dict) and battle.get("field_busy", {}).get("raw") == 2:
        state = _battle_wait(snapshot, battle)
    elif battle.get("active") is True:
        state = _battle_wait(snapshot, battle)
    elif context.get("is_dialogue_active") is True:
        state = _dialogue_wait(snapshot, context, semantic)
    else:
        player = snapshot.get("player") if isinstance(snapshot.get("player"), dict) else {}
        position = player.get("position") if isinstance(player.get("position"), dict) else {}
        grid = position.get("grid") if isinstance(position.get("grid"), dict) else {}
        if player.get("status") not in {"resolved", "candidate"} or not all(
            isinstance(grid.get(axis), int) for axis in ("x", "y", "z")
        ) or not isinstance(player.get("zone_id"), int):
            state = _unresolved(
                "player_position_unresolved",
                context={
                    "screen_type": normalize_screen_type(context.get("screen_type")),
                    "player_status": player.get("status"),
                    "zone_id": player.get("zone_id"),
                    "position": grid,
                },
            )
            state["observed"] = {
                "frame": transport.get("frame"),
                "session_id": transport.get("session_id"),
                "runtime_status": runtime.get("status"),
                "source": "RuntimeHub semantic snapshot; no screenshot required",
            }
            return state
        zone_id = player.get("zone_id")
        state = _base(
            status="ready",
            kind="decision",
            reason="overworld_action",
            decision_required=True,
            allowed_actions=["navigation.start", "interaction.inspect"],
            actions=[
                {"type": "navigation.start", "endpoint": "/api/v1/navigation/tasks"},
                {"type": "interaction.inspect", "endpoint": "/api/v1/agent/services/nearby"},
            ],
            auto_policy="pause",
            # Grid is deliberately excluded.  Navigation progress is already
            # represented by map events; a normal walk must not create an AI
            # decision event for every tile.
            key_parts=["overworld_action", zone_id, normalize_screen_type(context.get("screen_type"))],
            context={
                "screen_type": normalize_screen_type(context.get("screen_type")),
                "zone_id": zone_id,
                "position": (player.get("position") or {}).get("grid") if isinstance(player.get("position"), dict) else None,
                "active_task": snapshot.get("active_task"),
            },
        )

    transport = snapshot.get("transport") if isinstance(snapshot.get("transport"), dict) else {}
    state["observed"] = {
        "frame": transport.get("frame"),
        "session_id": transport.get("session_id"),
        "runtime_status": runtime.get("status"),
        "source": "RuntimeHub semantic snapshot; no screenshot required",
    }
    return state


__all__ = ["WAIT_FORMAT", "derive_wait_state"]
