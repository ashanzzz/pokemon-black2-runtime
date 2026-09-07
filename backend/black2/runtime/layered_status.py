"""Layered semantic game-state projection.

The legacy semantic engine exposes one ``screen_type``.  Real Black 2 state is
compositional: a field scene can be underneath dialogue, and battle can remain
active while its TextPrinter displays messages.  This module therefore models
orthogonal layers and separately chooses who owns the next input.
"""
from __future__ import annotations

from typing import Any


def _context(snapshot: dict[str, Any]) -> dict[str, Any]:
    semantic = snapshot.get("semantic") if isinstance(snapshot.get("semantic"), dict) else {}
    context = semantic.get("context") if isinstance(semantic.get("context"), dict) else {}
    return context


def _fresh(snapshot: dict[str, Any]) -> bool:
    transport = snapshot.get("transport") if isinstance(snapshot.get("transport"), dict) else {}
    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
    age = snapshot.get("age_seconds")
    return bool(
        transport.get("bridge_connected") is True
        and runtime.get("semantic_status") == "ready"
        and isinstance(age, (int, float))
        and 0 <= age <= 3
    )


def _layer(layer_id: str, active: bool | None, *, blocking: bool | None, priority: int,
           status: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": layer_id,
        "active": active,
        "blocking": blocking,
        "priority": priority,
        "status": status,
        "details": details or {},
    }


def project_layered_state(snapshot: dict[str, Any], battle: dict[str, Any]) -> dict[str, Any]:
    context = _context(snapshot)
    current = _fresh(snapshot)
    screen = str(context.get("screen_type") or "RUNTIME_UNRESOLVED").upper()
    dialogue_active = context.get("is_dialogue_active") if current else None
    dialogue_active = dialogue_active if isinstance(dialogue_active, bool) else None
    battle_active = battle.get("active") if isinstance(battle, dict) else None
    battle_active = battle_active if isinstance(battle_active, bool) else None
    loading = (battle.get("field_busy") or {}).get("raw") == 2 if isinstance(battle, dict) else False

    menu_screens = {"MAIN_MENU", "BAG_MENU", "PARTY_MENU"}
    menu_active = screen in menu_screens if current else None

    player = snapshot.get("player") if isinstance(snapshot.get("player"), dict) else {}
    has_field = bool(
        current
        and screen not in {"MAIN_MENU", "TITLE_SCREEN", "RUNTIME_UNRESOLVED"}
        and isinstance(player.get("zone_id"), int)
    )
    # Dialogue does not cancel exploration. Battle does: it is a separate
    # runtime scene, even though the field can remain allocated underneath.
    exploration_active = bool(has_field and battle_active is not True and menu_active is not True)

    if current:
        dialogue_status = "observed" if dialogue_active else "observed_inactive"
        menu_status = "observed" if menu_active else "observed_inactive"
        exploration_status = "derived_current" if exploration_active else "derived_inactive"
    else:
        dialogue_status = menu_status = exploration_status = "unresolved"

    battle_status = str(battle.get("active_status") or "unresolved") if isinstance(battle, dict) else "unresolved"
    transition_active: bool | None
    if loading:
        transition_active = True
    elif current:
        transition_active = False
    else:
        transition_active = None

    layers = [
        _layer("exploration", exploration_active if current else None,
               blocking=False if exploration_active else None, priority=20,
               status=exploration_status,
               details={
                   "zone_id": player.get("zone_id") if current else None,
                   "position": (player.get("position") or {}).get("grid") if current else None,
                   "can_move_player": context.get("can_move_player") if current else None,
               }),
        _layer("battle", battle_active, blocking=True if battle_active else None, priority=80,
               status=battle_status,
               details={
                   "kind": "unresolved",
                   "format": "unresolved",
                   "phase": "message" if battle_active and dialogue_active else "unresolved",
                   "field_busy": battle.get("field_busy") if isinstance(battle, dict) else None,
               }),
        _layer("menu", menu_active, blocking=True if menu_active else None, priority=70,
               status=menu_status, details={"screen_type": screen if current else None}),
        _layer("dialogue", dialogue_active, blocking=True if dialogue_active else None, priority=100,
               status=dialogue_status,
               details={
                   "text": context.get("dialogue_text") if current else None,
                   "full_text": context.get("full_dialogue_text") if current else None,
                   "speaker": context.get("speaker") if current else None,
                   "speaker_category": context.get("speaker_category") if current else None,
                   "choices": context.get("choices") if current else None,
                   "recommended_action": context.get("recommended_action") if current else None,
               }),
        _layer("transition", transition_active, blocking=True if transition_active else None, priority=110,
               status="candidate" if loading else "observed_inactive" if current else "unresolved",
               details={"field_busy": battle.get("field_busy") if isinstance(battle, dict) else None}),
    ]

    active_layers = [row["id"] for row in layers if row["active"] is True]
    overlays = [row["id"] for row in layers if row["active"] is True and row["id"] in {"dialogue", "menu", "transition"}]

    if transition_active:
        input_owner, input_kind, input_required = "transition", "wait", False
    elif dialogue_active:
        choices = context.get("choices") if current else None
        input_owner = "dialogue"
        input_kind = "dialogue_choice" if isinstance(choices, list) and choices else "advance_dialogue"
        input_required = bool(snapshot.get("semantic", {}).get("ready_for_input")) if isinstance(snapshot.get("semantic"), dict) else None
    elif battle_active:
        input_owner, input_kind, input_required = "battle", "battle_decision_unresolved", None
    elif menu_active:
        input_owner, input_kind, input_required = "menu", "menu_decision", True
    elif exploration_active:
        input_owner, input_kind, input_required = "exploration", "navigation_or_interaction", True
    else:
        input_owner, input_kind, input_required = None, "unresolved", None

    if battle_active:
        primary = "battle"
    elif exploration_active:
        primary = "exploration"
    elif menu_active:
        primary = "menu"
    elif transition_active:
        primary = "transition"
    else:
        primary = "unknown"

    semantic = snapshot.get("semantic") if isinstance(snapshot.get("semantic"), dict) else {}
    frame = semantic.get("frame") if current else battle.get("frame") if isinstance(battle, dict) else None

    return {
        "format": "black2-current-state/v2",
        "status": "current" if current else "partial" if battle_active is not None else "unresolved",
        "frame": frame,
        "primary_context": primary,
        "active_layers": active_layers,
        "overlays": overlays,
        "layers": layers,
        "input": {
            "owner": input_owner,
            "kind": input_kind,
            "required": input_required,
            "policy": "highest-priority active blocking layer owns input; layers themselves remain independently visible",
        },
        "exploration": {
            "active": exploration_active if current else None,
            "zone_id": player.get("zone_id") if current else None,
            "position": (player.get("position") or {}).get("grid") if current else None,
            "can_move": context.get("can_move_player") if current else None,
        },
        "dialogue": {
            "active": dialogue_active,
            "text": context.get("dialogue_text") if current else None,
            "full_text": context.get("full_dialogue_text") if current else None,
            "speaker": context.get("speaker") if current else None,
            "speaker_category": context.get("speaker_category") if current else None,
            "choices": context.get("choices") if current else None,
            "recommended_action": context.get("recommended_action") if current else None,
        },
        "battle": {
            "active": battle_active,
            "active_status": battle.get("active_status") if isinstance(battle, dict) else "unresolved",
            "kind": "unresolved",
            "context": "unresolved",
            "format": "unresolved",
            "phase": "message" if battle_active and dialogue_active else "unresolved",
            "decision_required": None,
            "execution_available": False,
            "party_header": battle.get("party_header") if isinstance(battle, dict) else None,
            "evidence_endpoint": "/api/v1/battle/evidence",
            "request_endpoint": "/api/v1/battle/request",
        },
        "legacy": {
            "screen_type": screen if current else "RUNTIME_UNRESOLVED",
            "note": "screen_type is retained for compatibility and must not be treated as an exclusive scene model",
        },
        "evidence": {
            "runtime_snapshot_current": current,
            "battle": battle,
            "layer_policy": "non-exclusive",
        },
    }
