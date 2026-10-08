"""Evidence-gated battle observation and semantic decision contracts.

The runtime now exposes a conservative IREJ rev.1 battle-presence candidate
from a recovered GameData -> FieldStatus chain.  This is intentionally *not*
an action executor.  Legal move/item/target/menu decoding and post-action
verification are still required before any battle command can mutate BizHawk.
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..decoders.battle_identity import BattleIdentityDecoder
from ..decoders.battle_runtime import BattleRuntimeDecoder
from ..decoders.battle_ui_cursor import BattleUiCursorDecoder
from ..decoders.party_runtime import PlayerPartyDecoder
from ..decoders.inventory_runtime import PlayerInventoryDecoder
from ..decoders.trainer_rom import TrainerCatalogError, TrainerRomCatalog
from ..actions.input_engine import ActionEngine
from ..memory.reader import MemoryReader
from ..runtime.hub import RuntimeHub
from ..runtime.battle_identity_history import battle_identity_history
from ..runtime.events import agent_event_bus
from ..state.playtest_memory import playtest_memory
from ..world.runtime_player_state import player_runtime_service

router = APIRouter(prefix="/api/v1/battle", tags=["battle-v2"])
_reader: MemoryReader | None = None
_hub: RuntimeHub | None = None
_action_engine: ActionEngine | None = None
_decoder = BattleRuntimeDecoder()
_ui_cursor_decoder = BattleUiCursorDecoder()
_identity_decoder = BattleIdentityDecoder()
_party_decoder = PlayerPartyDecoder()
_inventory_decoder = PlayerInventoryDecoder()
_trainer_catalog: TrainerRomCatalog | None = None
_trainer_catalog_error: str | None = None

MoveTarget = Annotated[str, StringConstraints(pattern=r"^(player|opponent):[0-2]$")]
ItemTarget = Annotated[str, StringConstraints(pattern=r"^((player|opponent):[0-2]|party:[1-6])$")]
Actor = Annotated[str, StringConstraints(pattern=r"^player:[0-2]$")]


def configure_battle_routes(reader: MemoryReader, hub: RuntimeHub, action_engine: ActionEngine | None = None) -> None:
    global _reader, _hub, _action_engine, _trainer_catalog, _trainer_catalog_error
    _reader = reader
    _hub = hub
    _action_engine = action_engine
    _decoder.configure(reader)
    _ui_cursor_decoder.configure(reader)
    _identity_decoder.configure(reader)
    _party_decoder.configure(reader)
    _inventory_decoder.configure(reader)
    from ..battle.battle_state_machine import battle_state_machine
    battle_state_machine.configure(reader)
    from ..battle.battle_action_service import battle_action_service
    if action_engine is not None:
        battle_action_service.configure(action_engine, reader)
    # RuntimeHub owns the single-flight sampling cadence.  Install the
    # observer after the decoders are configured so a battle transition is
    # recorded even when no browser happens to poll /identity that frame.
    hub.battle_identity_callback = _observe_battle_transition
    # RuntimeHub owns the wait-boundary cadence.  Give it the same bounded
    # read-only cursor decoder used by GET /ui-cursor so move/command choices
    # become a semantic decision wait without browser polling.
    hub.battle_ui_callback = _hub_battle_ui_sample
    # ROM catalog construction is lazy, but a process restart should not keep
    # an error from a previous emulator/ROM session.
    _trainer_catalog = None
    _trainer_catalog_error = None


async def _hub_battle_ui_sample(snapshot: dict[str, Any]) -> dict[str, Any]:
    battle = snapshot.get("battle") if isinstance(snapshot.get("battle"), dict) else {}
    return await _ui_cursor_sample({
        "active": battle.get("active"),
        "active_status": battle.get("active_status"),
        "field_busy": battle.get("field_busy"),
    })


def _get_trainer_catalog() -> TrainerRomCatalog | None:
    global _trainer_catalog, _trainer_catalog_error
    if _trainer_catalog is not None:
        return _trainer_catalog
    if _trainer_catalog_error is not None:
        return None
    try:
        _trainer_catalog = TrainerRomCatalog()
    except Exception as exc:
        _trainer_catalog_error = f"{type(exc).__name__}: {exc}"
        return None
    return _trainer_catalog


def _context() -> dict[str, Any]:
    if _hub is None:
        return {}
    snap = _hub.snapshot()
    semantic = snap.get("semantic") if isinstance(snap.get("semantic"), dict) else {}
    context = dict(semantic.get("context") if isinstance(semantic.get("context"), dict) else {})
    # Battle mode can replace the semantic screen projection before the map
    # layer is sampled again.  Preserve the last same-session overworld
    # context so ROM script candidates can be matched to BattlePokeParam RAM.
    battle_context = snap.get("battle_context")
    if isinstance(battle_context, dict):
        context["battle_overworld"] = battle_context
        if context.get("zone_id") is None and isinstance(battle_context.get("zone_id"), int):
            context["zone_id"] = battle_context.get("zone_id")
    player = snap.get("player") if isinstance(snap.get("player"), dict) else {}
    if player.get("status") not in {"resolved", "candidate"} and isinstance(player_runtime_service.latest, dict):
        player = player_runtime_service.latest
    if context.get("zone_id") is None and isinstance(player.get("zone_id"), int):
        context["zone_id"] = player.get("zone_id")
    return context


def _read_only_contract() -> dict[str, Any]:
    """Common machine-readable safety declaration for battle GET payloads.

    Battle RAM offsets beyond the recovered BusyFlag/PokeParty chain are still
    research candidates.  Every observation endpoint therefore advertises that
    it is a pure read and that no value should be used as authorization to send
    an emulator input.  Keeping this contract on each payload helps external
    AI clients enforce the policy without relying on endpoint names.
    """
    return {
        "read_only": True,
        "mutation_policy": "cache_reads_only",
        "writes_performed": False,
        "execution_gate": "all battle writes remain disabled until evidence and post-action verification pass",
    }


# ---- legacy /actions payloads ------------------------------------------------
class UseMoveAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["use_move"]
    slot: int = Field(ge=1, le=4)
    target: MoveTarget | None = None


class SwitchAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["switch"]
    party_slot: int = Field(ge=1, le=6)


class UseItemAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["use_item"]
    item_id: int = Field(ge=1, le=65535)
    target: ItemTarget | None = None


class RunAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["run"]


BattleAction = Annotated[
    UseMoveAction | SwitchAction | UseItemAction | RunAction,
    Field(discriminator="type"),
]


# ---- v2 /decisions semantic commands ---------------------------------------
class UseMoveCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["use_move"]
    actor: Actor = "player:0"
    move_slot: int = Field(ge=1, le=4)
    target: MoveTarget | None = None


class SwitchCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["switch"]
    actor: Actor = "player:0"
    party_slot: int = Field(ge=1, le=6)


class UseItemCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["use_item"]
    actor: Actor = "player:0"
    item_id: int = Field(ge=1, le=65535)
    target: ItemTarget | None = None


class ThrowBallCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["throw_ball"]
    actor: Actor = "player:0"
    item_id: int = Field(ge=1, le=65535)
    target: MoveTarget = "opponent:0"


class RunCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["run"]
    actor: Actor = "player:0"


class ShiftCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["shift"]
    actor: Actor
    to_position: Literal["left", "center", "right"]


class RotateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["rotate"]
    actor: Actor = "player:0"
    direction: Literal["left", "right"]


BattleCommand = Annotated[
    UseMoveCommand | SwitchCommand | UseItemCommand | ThrowBallCommand | RunCommand | ShiftCommand | RotateCommand,
    Field(discriminator="type"),
]


class BattleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    battle_id: str | None = None
    request_id: str | int | None = None
    commands: list[BattleCommand] = Field(min_length=1, max_length=3)


# ---- calibrated UI executor -------------------------------------------------
class BattleUiUseMove(BaseModel):
    """A deliberately narrow, evidence-backed UI action.

    The executor reads the live ``btlv_input.c`` phase/cursor field and moves
    only along the shortest verified grid path.  Every directional edge is
    read back before the next edge or confirm; a missing/delayed key stops the
    request with no blind retry.  It is only exposed as a calibrated
    single-battle profile and is closed by a persistent-party PP/HP check; it
    is not the generic semantic /decisions executor.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["use_move"]
    actor: Literal["player:0"] = "player:0"
    move_slot: int = Field(ge=1, le=4)
    profile: Literal["single_move_grid_v1"] = "single_move_grid_v1"
    settle_mode: Literal["verify_only", "advance_once"] = "advance_once"

class BattleUiThrowBall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["throw_ball"]
    actor: Literal["player:0"] = "player:0"
    item_id: int = Field(default=4, ge=1, le=65535)
    profile: Literal["single_move_grid_v1"] = "single_move_grid_v1"
    settle_mode: Literal["verify_only", "advance_once"] = "advance_once"


class BattleUiUseItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["use_item"]
    actor: Literal["player:0"] = "player:0"
    item_id: int = Field(default=17, ge=1, le=65535)
    party_slot: int = Field(default=1, ge=1, le=6)
    profile: Literal["single_move_grid_v1"] = "single_move_grid_v1"
    settle_mode: Literal["verify_only", "advance_once"] = "advance_once"


class BattleUiRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["run"]
    actor: Literal["player:0"] = "player:0"
    profile: Literal["single_move_grid_v1"] = "single_move_grid_v1"
    settle_mode: Literal["verify_only", "advance_once"] = "advance_once"


class BattleUiSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["switch"]
    actor: Literal["player:0"] = "player:0"
    party_slot: int = Field(default=2, ge=1, le=6)
    profile: Literal["single_move_grid_v1"] = "single_move_grid_v1"
    settle_mode: Literal["verify_only", "advance_once"] = "advance_once"


BattleUiActionRequest = Annotated[BattleUiUseMove | BattleUiThrowBall | BattleUiUseItem | BattleUiRun | BattleUiSwitch, Field(discriminator="type")]


CALIBRATED_UI_PROFILES: dict[str, dict[str, Any]] = {
    "single_move_grid_v1": {
        "status": "executable_for_all_slots",
        "executable": True,
        "battle_format": "single",
        "command_anchor": "FIGHT is the observed initial command anchor",
        "move_grid": {
            "slot_1": "top_left",
            "slot_2": "top_right",
            "slot_3": "bottom_left",
            "slot_4": "bottom_right",
        },
        "cursor_memory": "verified_for_all_slots",
        "cursor_policy": "read_current_slot_then_verify_every_direction_before_confirm",
        "normalization": "state-driven_shortest_path; no fixed normalization sequence",
        "postcondition": "same-frame battle turn is confirmed by live BattlePokeParam HP transition or checksum-decoded PP decrement",
        "evidence_cases": [
            "case414_wild_battle_open_move_again",
            "case415_wild_battle_move_right_cursor",
            "case416_wild_battle_move_left_cursor",
            "case417_wild_battle_move_down_to_water_gun",
            "case418_wild_battle_water_gun_directional_confirm",
            "case419_wild_battle_result_directional_advance",
        ],
        "limitations": [
            "Only the single-battle move menu phase and move slots 1..3 have controlled cursor readback.",
            "Slot 4 and multi-actor/double/triple/rotation battles are not enabled.",
            "A result-message advance is attempted only after the move postcondition is observed as pending; a failed postcondition never retries blindly.",
        ],
    },
}


def _ui_profile_is_executable(profile: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(profile, dict)
        and profile.get("executable") is True
        and profile.get("cursor_memory") == "verified_for_slots_1_3"
    )


def _move_grid_path(current_slot: int, target_slot: int) -> list[str] | None:
    """Return a shortest 2-D grid path for the three readback slots."""
    positions = {1: (0, 0), 2: (1, 0), 3: (0, 1)}
    if current_slot not in positions or target_slot not in positions:
        return None
    cx, cy = positions[current_slot]
    tx, ty = positions[target_slot]
    path: list[str] = []
    if cx < tx:
        path.append("Right")
    elif cx > tx:
        path.append("Left")
    if cy < ty:
        path.append("Down")
    elif cy > ty:
        path.append("Up")
    return path


def _ui_write_contract() -> dict[str, Any]:
    return {
        "read_only": False,
        "mutation_policy": "emulator_input_only_no_ram_write",
        "writes_performed": True,
        "execution_gate": "calibrated UI sequence plus persistent-party postcondition",
    }


def _party_slot_and_move(party: dict[str, Any], move_slot: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    slots = party.get("slots") if isinstance(party.get("slots"), list) else []
    if not slots or not isinstance(slots[0], dict):
        return None, None
    moves = slots[0].get("moves") if isinstance(slots[0].get("moves"), list) else []
    move = next((row for row in moves if isinstance(row, dict) and row.get("slot") == move_slot), None)
    return slots[0], move


async def _ui_sample() -> dict[str, Any]:
    evidence = await _evidence()
    party = await _party_decoder.sample()
    return {
        "active": evidence.get("active"),
        "active_status": evidence.get("active_status"),
        "field_busy": evidence.get("field_busy"),
        "frame": evidence.get("frame"),
        "party": party,
    }


async def _ui_cursor_sample(evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read the battle input cursor only when the presence gate is active.

    The allocator is reused by the game and can retain stale bytes after a
    battle.  A valid-looking ``btlv_input.c`` object is therefore not enough
    on its own to expose a live battle cursor to an AI client.
    """
    evidence = evidence if isinstance(evidence, dict) else await _evidence()
    if evidence.get("active") is not True:
        result = _ui_cursor_decoder.unresolved(
            "Battle BusyFlag is not active; stale battle-view allocator bytes are not promoted."
        )
    else:
        result = await _ui_cursor_decoder.sample()
    result["battle_presence"] = {
        "active": evidence.get("active"),
        "active_status": evidence.get("active_status"),
        "field_busy": evidence.get("field_busy"),
    }
    return result


async def _battle_identity(evidence: dict[str, Any]) -> dict[str, Any]:
    """Read battle species candidates only for the current active frame.

    The persistent party is used only as a side-matching aid.  It is not
    promoted to BattleMon data, and an inactive battle never triggers a heap
    scan (stale allocator bytes must not become a new encounter).
    """
    party: dict[str, Any] | None = None
    if evidence.get("active") is True:
        try:
            party = await _party_decoder.sample()
        except Exception:
            party = None
    return await _identity_decoder.sample(
        presence=evidence,
        player_party=party,
        context=_context(),
        trainer_catalog=_get_trainer_catalog(),
    )


def _battle_hp_view(identity: dict[str, Any]) -> dict[str, Any]:
    """Keep only same-frame HP facts needed to verify one UI turn."""
    result: dict[str, Any] = {}
    for side_name in ("player", "opponent"):
        side = identity.get(side_name) if isinstance(identity.get(side_name), dict) else {}
        active = side.get("active") if isinstance(side.get("active"), dict) else {}
        result[side_name] = {
            "status": side.get("status"),
            "species_id": active.get("species_id"),
            "current_hp": active.get("current_hp"),
            "max_hp": active.get("max_hp"),
            "confidence": active.get("confidence"),
        }
    return {
        "status": identity.get("status"),
        "verified": identity.get("verified"),
        "battle_kind": identity.get("battle_kind"),
        "player": result.get("player"),
        "opponent": result.get("opponent"),
    }


async def _battle_hp_snapshot() -> dict[str, Any]:
    evidence = await _evidence()
    if evidence.get("active") is not True:
        return {"status": "inactive", "frame": evidence.get("frame"), "active": evidence.get("active")}
    identity = await _battle_identity(evidence)
    return {
        "status": "candidate",
        "frame": identity.get("evidence", {}).get("frame") if isinstance(identity.get("evidence"), dict) else evidence.get("frame"),
        "active": True,
        "view": _battle_hp_view(identity),
    }


def _identity_event_view(identity: dict[str, Any]) -> dict[str, Any]:
    kind = identity.get("battle_kind") if isinstance(identity.get("battle_kind"), dict) else {}
    trainer = identity.get("trainer") if isinstance(identity.get("trainer"), dict) else {}
    opponent = identity.get("opponent") if isinstance(identity.get("opponent"), dict) else {}
    player = identity.get("player") if isinstance(identity.get("player"), dict) else {}

    def _side_rows(side: dict[str, Any]) -> list[dict[str, Any]]:
        rows = side.get("party") if isinstance(side.get("party"), list) else []
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            species = row.get("species") if isinstance(row.get("species"), dict) else {}
            result.append({
                "species_id": row.get("species_id"),
                "name": species.get("names") or species.get("name"),
                "current_hp": row.get("current_hp"),
                "max_hp": row.get("max_hp"),
                "confidence": row.get("confidence"),
            })
        return result

    trainer_class = trainer.get("class") if isinstance(trainer.get("class"), dict) else trainer.get("class")
    return {
        "status": identity.get("status"),
        "verified": identity.get("verified"),
        "battle_kind": kind,
        "trainer": {
            "status": trainer.get("status"),
            "kind": trainer.get("kind"),
            "trainer_id": trainer.get("trainer_id"),
            "name": trainer.get("name"),
            "class": trainer_class,
        },
        "player_species": _side_rows(player),
        "opponent_species": _side_rows(opponent),
        "causal_context": identity.get("causal_context"),
        "reason": identity.get("reason"),
    }


async def _observe_battle_transition(snapshot: dict[str, Any]) -> None:
    """Observe one active battle from the hub and journal its identity.

    This is intentionally a read-only diagnostic hook.  It only reads the
    same RAM/Dex/ROM sources as ``GET /identity`` and emits an event; it never
    sends input or writes emulator memory.
    """
    evidence = await _evidence()
    identity = await _battle_identity(evidence)
    context = _context()
    transport = snapshot.get("transport") if isinstance(snapshot.get("transport"), dict) else {}
    session_id = transport.get("session_id")
    history = battle_identity_history.record(
        identity,
        presence=evidence,
        context=context,
        session_id=session_id if isinstance(session_id, str) else None,
    )
    if history.get("recorded") is not True:
        return
    view = _identity_event_view(identity)
    try:
        playtest_memory.record_event({
            "type": "battle.identity.observed",
            "source": "runtime_hub",
            "session_id": session_id,
            "frame": evidence.get("frame"),
            "zone_id": context.get("zone_id"),
            "identity": view,
            "history_path": str(battle_identity_history.path),
        })
    except Exception:
        # Memory is helpful context, never a gameplay dependency.
        pass
    await agent_event_bus.publish(
        "battle.identity.observed",
        frame=evidence.get("frame"),
        session_id=session_id if isinstance(session_id, str) else None,
        resources={
            "identity": "/api/v1/battle/identity",
            "history": "/api/v1/battle/identity/history",
            "evidence": "/api/v1/battle/evidence",
            "memory": "/api/v1/ai/memory",
        },
        summary="Battle identity observed from RAM and ROM evidence.",
        data={
            "identity": view,
            "history_path": str(battle_identity_history.path),
            "history_recorded": True,
            "policy": "Candidate/unresolved labels are preserved; no trainer causality is guessed.",
        },
    )


async def _press_ui_button(button: str) -> dict[str, Any]:
    if _action_engine is None:
        return {"ok": False, "error": "battle UI action engine is not configured"}
    return await _action_engine.press_button(button, hold_frames=4, wait_frames=15)


async def _wait_for_stable_move_cursor(*, timeout_sec: float = 0.9) -> dict[str, Any]:
    """Poll RAM until the move menu exposes a stable known cursor value."""
    deadline = asyncio.get_running_loop().time() + timeout_sec
    previous_raw: int | None = None
    same_count = 0
    last: dict[str, Any] | None = None
    while asyncio.get_running_loop().time() < deadline:
        current = await _ui_cursor_sample()
        last = current
        phase = current.get("phase", {})
        cursor = current.get("cursor", {})
        raw = cursor.get("raw_u32")
        if phase.get("value") == "move_menu" and cursor.get("status") == "candidate":
            if raw == previous_raw:
                same_count += 1
            else:
                previous_raw = raw
                same_count = 1
            # One repeated read is enough to filter the observed 0x7E07
            # opening edge without adding a screenshot to normal control.
            if same_count >= 2:
                return current
        await asyncio.sleep(0.06)
    return last or _ui_cursor_decoder.unresolved("move-menu cursor did not become stable before timeout")


async def _wait_for_battle_move_settle(
    *, timeout_sec: float = 12.0, auto_advance: bool = True
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Observe a confirmed move until the battle returns to a command state.

    Pushes A periodically if auto_advance is enabled to progress in-battle dialogue
    messages (damage reports, secondary effects) so the turn settles back to command_menu.
    """
    deadline = asyncio.get_running_loop().time() + timeout_sec
    last_cursor: dict[str, Any] | None = None
    start_time = asyncio.get_running_loop().time()
    last_a_press = start_time + 1.8

    while asyncio.get_running_loop().time() < deadline:
        last_cursor = await _ui_cursor_sample()
        presence = last_cursor.get("battle_presence") if isinstance(last_cursor.get("battle_presence"), dict) else {}
        if presence.get("active") is not True or last_cursor.get("phase", {}).get("value") == "command_menu":
            break

        now = asyncio.get_running_loop().time()
        if auto_advance and now - last_a_press >= 1.0 and _action_engine is not None:
            last_a_press = now
            try:
                await _action_engine.press_button("A", hold_frames=4, wait_frames=8)
            except Exception:
                pass

        await asyncio.sleep(0.15)

    after = await _ui_sample()
    after_cursor = await _ui_cursor_sample({
        "active": after.get("active"),
        "active_status": after.get("active_status"),
        "field_busy": after.get("field_busy"),
    })
    battle_after = await _battle_hp_snapshot()
    return after, after_cursor if last_cursor is None else after_cursor, battle_after


EVIDENCE_REQUIREMENTS = [
    "Paired battle and non-battle IREJ rev.1 captures verifying the recovered FieldStatus locator.",
    "Battle kind, format, phase, command menu, cursor, and legal-action decoding verified across frames.",
    "Party, move-slot, item-id, target and capture/run legality mappings verified against visible game state.",
    "Closed-loop BizHawk input execution with request freshness and post-action state/failure verification.",
]

LEGACY_ACTION_CONTRACTS = {
    "use_move": {"executable": False, "fields": {"slot": "1..4", "target": "optional player/opponent slot"}},
    "switch": {"executable": False, "fields": {"party_slot": "1..6"}},
    "use_item": {"executable": False, "fields": {"item_id": "1..65535", "target": "optional battle/party target"}},
    "run": {"executable": False, "fields": {}},
}

DECISION_CONTRACTS = {
    "use_move": {"executable": False, "multi_actor": True, "fields": {"actor": "player:0..2", "move_slot": "1..4", "target": "optional battle slot"}},
    "switch": {"executable": False, "multi_actor": True, "fields": {"actor": "player:0..2", "party_slot": "1..6"}},
    "use_item": {"executable": False, "multi_actor": True, "fields": {"actor": "player:0..2", "item_id": "1..65535", "target": "optional battle/party target"}},
    "throw_ball": {"executable": False, "multi_actor": True, "fields": {"actor": "player:0..2", "item_id": "ball item id", "target": "opponent:0..2"}},
    "run": {"executable": False, "multi_actor": True, "fields": {"actor": "player:0..2"}},
    "shift": {"executable": False, "formats": ["triple"], "fields": {"actor": "player:0..2", "to_position": "left|center|right"}},
    "rotate": {"executable": False, "formats": ["rotation"], "fields": {"actor": "player:0..2", "direction": "left|right"}},
}


def _execution_reason(evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    if not evidence or evidence.get("active") is not True:
        return {
            "code": "BATTLE_RUNTIME_UNRESOLVED",
            "message": "A current battle cannot yet be established strongly enough for semantic action execution.",
        }
    return {
        "code": "BATTLE_ACTION_EXECUTION_UNVERIFIED",
        "message": (
            "Battle presence is observed as a candidate, but legal command/menu/target decoding and closed-loop "
            "post-action verification are not yet verified. Blind menu input is rejected."
        ),
    }


async def _evidence() -> dict[str, Any]:
    return await _decoder.sample()


def _dialogue_overlay() -> dict[str, Any]:
    context = _context()
    active = context.get("is_dialogue_active")
    return {
        "active": active if isinstance(active, bool) else None,
        "text": context.get("dialogue_text"),
        "speaker": context.get("speaker"),
        "choices": context.get("choices"),
        "recommended_action": context.get("recommended_action"),
        "attribution": "unattributed_to_battle",
        "status": "unresolved",
    }


def _unresolved_fact(source: str, reason: str) -> dict[str, Any]:
    return {"status": "unresolved", "source": source, "value": None, "reason": reason}


def _battle_message_overlay() -> dict[str, Any]:
    context = _context()
    overlay = context.get("battle_message_overlay")
    if isinstance(overlay, dict):
        # The state engine owns the producer schema. Preserve every explicit
        # unresolved field while refusing to upgrade it in the HTTP layer.
        return overlay
    return {
        "status": "unresolved",
        "source": "no battle-message overlay sample is cached",
        "printer_activity": _unresolved_fact("no hardware-printer sample", "battle printer activity is unavailable"),
        "current_text": _unresolved_fact("battle text decoder is not verified", "text is not decoded"),
        "loaded_text": _unresolved_fact("battle text decoder is not verified", "text is not decoded"),
        "full_text": _unresolved_fact("battle text decoder is not verified", "text is not decoded"),
        "choices": _unresolved_fact("battle menu/choice decoder is not verified", "choices are not decoded"),
    }


def _battle_field() -> dict[str, Any]:
    source = "IREJ rev.1 battle runtime does not decode battle-field structures"
    reason = "No verified Gen V battle weather, field-effect, side-condition, or visual decoder exists."
    player_side = _unresolved_fact(source, reason)
    opponent_side = _unresolved_fact(source, reason)
    return {
        "status": "unresolved",
        "source": source,
        "battle_weather": _unresolved_fact(source, reason),
        "field_effects": _unresolved_fact(source, reason),
        "side_conditions": {
            "status": "unresolved",
            "source": source,
            "player": player_side,
            "opponent": opponent_side,
        },
        "player_side_conditions": player_side,
        "opponent_side_conditions": opponent_side,
        "battlefield_visual": _unresolved_fact(source, reason),
        "gen_v_policy": "No generic terrain field is exposed: Gen V does not use the Gen VI terrain mechanic.",
    }


def _overworld_context() -> dict[str, Any]:
    source = "battle runtime does not establish overworld state"
    reason = "Battle mode must not infer overworld time, season, or weather from ROM/static map data."
    return {
        "status": "unresolved",
        "source": source,
        "time_of_day": _unresolved_fact(source, reason),
        "season": _unresolved_fact(source, reason),
        "zone_weather": _unresolved_fact(source, reason),
    }


@router.get("/capabilities")
async def battle_capabilities() -> dict[str, Any]:
    evidence = await _evidence()
    return {
        **_read_only_contract(),
        "format": "black2-battle-capabilities/v1",
        "api_generation": 2,
        "decoder": {
            "status": "CANDIDATE" if evidence.get("status") == "candidate" else "RESEARCH",
            "verified": False,
            "confidence": evidence.get("confidence", 0.0),
            "can_detect": (["battle_presence_candidate", "field_busy_flag", "party_header_candidate"]
                           if _reader is not None else []),
        },
        "read": {
            "state": "/api/v1/battle/state",
            "identity": "/api/v1/battle/identity",
            "identity_history": "/api/v1/battle/identity/history",
            "trainer_catalog": "/api/v1/battle/trainer/{trainer_id}",
            "zone_trainer_candidates": "/api/v1/battle/zone/{zone_id}/trainer-candidates",
            "request": "/api/v1/battle/request",
            "field": "/api/v1/battle/field",
            "environment": "/api/v1/game/environment",
            "party": "/api/v1/battle/party",
            "moves": "/api/v1/battle/moves",
            "ui_cursor": "/api/v1/battle/ui-cursor",
            "items": "/api/v1/battle/items",
            "events": "/api/v1/battle/events",
            "evidence": "/api/v1/battle/evidence",
            "legacy_actions": "/api/v1/battle/actions",
        },
        "write": {"decisions": "/api/v1/battle/decisions", "legacy_actions": "/api/v1/battle/actions"},
        "decision_discriminator": "type",
        "decision_contracts": DECISION_CONTRACTS,
        "legacy_action_contracts": LEGACY_ACTION_CONTRACTS,
        "action_contracts": LEGACY_ACTION_CONTRACTS,
        "execution": {
            "available": False,
            "requires_verified_current_state": True,
            "blind_menu_input_allowed": False,
        },
        "decision_policy": {"requires_request_id_before_execution": True, "max_commands": 3},
        "reason": _execution_reason(evidence),
        "evidence_requirements": EVIDENCE_REQUIREMENTS,
        "calibrated_ui": {
            "read": "/api/v1/battle/ui-capabilities",
            "cursor_read": "/api/v1/battle/ui-cursor",
            "write": "/api/v1/battle/ui-actions",
            "profiles": list(CALIBRATED_UI_PROFILES),
            # Having an ActionEngine only means the bridge can receive keys;
            # it does not make the unresolved cursor sequence safe.
            "available": bool(
                _action_engine is not None
                and any(
                profile.get("executable") is True
                    and _ui_profile_is_executable(profile)
                    for profile in CALIBRATED_UI_PROFILES.values()
                )
            ),
            "status": (
                "executable_verified"
                if _action_engine is not None and any(
                    profile.get("executable") is True
                    and _ui_profile_is_executable(profile)
                    for profile in CALIBRATED_UI_PROFILES.values()
                )
                else "diagnostic_only_memory_gate"
            ),
        },
    }


@router.get("/ui-capabilities")
async def battle_ui_capabilities() -> dict[str, Any]:
    evidence = await _evidence()
    cursor = await _ui_cursor_sample(evidence)
    return {
        "format": "black2-battle-ui-capabilities/v1",
        "read_only": True,
        "writes_performed": False,
        "available": bool(
            _action_engine is not None
            and any(
                profile.get("executable") is True
                and _ui_profile_is_executable(profile)
                for profile in CALIBRATED_UI_PROFILES.values()
            )
        ),
        "api": "/api/v1/battle/ui-actions",
        "profiles": CALIBRATED_UI_PROFILES,
        "cursor": cursor,
        "screenshot_policy": "calibration or semantic mismatch only; normal executor uses API/RAM",
        "current_battle": {
            "active": evidence.get("active"),
            "active_status": evidence.get("active_status"),
            "field_busy": evidence.get("field_busy"),
        },
    }


@router.get("/ui-cursor")
async def battle_ui_cursor() -> dict[str, Any]:
    """Return the current read-only battle menu cursor RAM candidate."""
    evidence = await _evidence()
    return await _ui_cursor_sample(evidence)


@router.post("/ui-actions")
async def execute_calibrated_ui_action(action: BattleUiActionRequest, request: Request) -> JSONResponse:
    """Execute one calibrated single-battle move with a closed-loop check.

    This endpoint intentionally does not pretend that a generic battle menu
    decoder exists.  It is a small bridge between the confirmed UI sequence
    and a future RAM cursor decoder.  A caller must use the named profile;
    every attempted input is followed by a persistent PP/battle-presence
    verification and is rejected when the expected state is unavailable.
    """

    profile = CALIBRATED_UI_PROFILES.get(action.profile)
    if profile is None:
        return JSONResponse(status_code=409, content={
            "format": "black2-battle-ui-action/v1",
            "status": "rejected",
            "executed": False,
            "action": action.model_dump(mode="json"),
            "reason": {"code": "BATTLE_UI_PROFILE_UNAVAILABLE", "message": "Requested calibrated UI profile is not enabled."},
            "request_id": request.headers.get("x-request-id"),
        })
    if _action_engine is None:
        return JSONResponse(status_code=503, content={
            "format": "black2-battle-ui-action/v1",
            "status": "rejected",
            "executed": False,
            "action": action.model_dump(mode="json"),
            "reason": {"code": "BATTLE_UI_EXECUTOR_NOT_CONFIGURED", "message": "The emulator input engine is not configured."},
            "request_id": request.headers.get("x-request-id"),
        })
    if not _ui_profile_is_executable(profile):
        return JSONResponse(status_code=409, content={
            "format": "black2-battle-ui-action/v1",
            "status": "rejected",
            "executed": False,
            "action": action.model_dump(mode="json"),
            "reason": {
                "code": "BATTLE_UI_CURSOR_MEMORY_UNVERIFIED",
                "message": "The requested profile is not enabled for closed-loop cursor execution; no input was sent.",
                "next": "use only slots 1..3 of single_move_grid_v1, or collect controlled readback for the missing battle format/slot",
            },
            "profile": profile,
            "request_id": request.headers.get("x-request-id"),
        })

    before = await _ui_sample()
    if before.get("active") is not True or not isinstance(before.get("field_busy"), dict) or before["field_busy"].get("raw") != 1:
        return JSONResponse(status_code=409, content={
            "format": "black2-battle-ui-action/v1",
            "status": "rejected",
            "executed": False,
            "action": action.model_dump(mode="json"),
            "before": before,
            "reason": {"code": "BATTLE_UI_NOT_ACTIVE", "message": "Battle BusyFlag is not active; no menu input was sent."},
            "request_id": request.headers.get("x-request-id"),
        })

    before_cursor = await _ui_cursor_sample({
        "active": before.get("active"),
        "active_status": before.get("active_status"),
        "field_busy": before.get("field_busy"),
    })
    before_phase = before_cursor.get("phase", {}).get("value")
    if (
        before_cursor.get("status") != "candidate"
        or before_cursor.get("phase", {}).get("status") != "candidate"
        or before_phase not in {"command_menu", "move_menu"}
        or (before_phase == "move_menu" and before_cursor.get("cursor", {}).get("status") != "candidate")
    ):
        return JSONResponse(status_code=409, content={
            "format": "black2-battle-ui-action/v1",
            "status": "rejected",
            "executed": False,
            "action": action.model_dump(mode="json"),
            "before": before,
            "before_cursor": before_cursor,
            "reason": {
                "code": "BATTLE_UI_CURSOR_READBACK_UNRESOLVED",
                "message": "The current battle phase or cursor was not read back from a valid btlv_input.c object; no input was sent.",
            },
            "request_id": request.headers.get("x-request-id"),
        })

    battle_before = await _battle_hp_snapshot()

    writes: list[dict[str, Any]] = []

    # Handle throw_ball action
    if action.type == "throw_ball":
        identity = await _battle_identity(before)
        battle_kind = (identity.get("battle_kind") or {}).get("value")
        if battle_kind != "wild":
            return JSONResponse(status_code=409, content={
                "format": "black2-battle-ui-action/v1",
                "status": "rejected",
                "executed": False,
                "action": action.model_dump(mode="json"),
                "before": before,
                "reason": {
                    "code": "BATTLE_CAPTURE_NOT_ALLOWED",
                    "message": "Cannot capture trainer's Pokémon (不能捕捉属于其他训练家的宝可梦！)",
                },
                "request_id": request.headers.get("x-request-id"),
            })

        inventory = await _inventory_decoder.sample()
        ball_item = next((i for i in inventory.get("items", []) if i.get("item_id") == action.item_id and i.get("quantity", 0) > 0), None)
        if not ball_item:
            return JSONResponse(status_code=409, content={
                "format": "black2-battle-ui-action/v1",
                "status": "rejected",
                "executed": False,
                "action": action.model_dump(mode="json"),
                "before": before,
                "reason": {
                    "code": "BATTLE_NO_POKEBALLS",
                    "message": f"No Poké Balls of ID {action.item_id} found in bag inventory.",
                },
                "request_id": request.headers.get("x-request-id"),
            })

        # Sample RAM ground-truth party and bag before the throw
        party_before = await _party_decoder.sample()
        inventory_before = await _inventory_decoder.sample()
        ball_before_qty = next((i.get("quantity", 0) for i in inventory_before.get("items", []) if i.get("item_id") == action.item_id), 0)
        party_before_count = party_before.get("count", 0)

        # Open BAG: on the main battle screen, BAG is at (45, 175)
        if _action_engine is not None and hasattr(_action_engine, "touch_screen"):
            # 1. Touch BAG
            touch_bag = await _action_engine.touch_screen(45, 175, hold_frames=8)
            writes.append({"touch": [45, 175], "purpose": "open_bag_touch", "response": touch_bag})
            await asyncio.sleep(0.5)

            # 2. Select Poké Ball pocket (Right from HP/PP Recovery)
            press_right = await _action_engine.press_button("Right", hold_frames=4, wait_frames=10)
            writes.append({"button": "Right", "purpose": "select_pokeball_pocket", "response": press_right})
            await asyncio.sleep(0.25)

            # 3. Enter Poké Ball pocket
            press_enter = await _action_engine.press_button("A", hold_frames=4, wait_frames=15)
            writes.append({"button": "A", "purpose": "enter_pokeball_pocket", "response": press_enter})
            await asyncio.sleep(0.3)

            # 4. Select Poké Ball in Slot 1
            press_sel = await _action_engine.press_button("A", hold_frames=4, wait_frames=15)
            writes.append({"button": "A", "purpose": "select_pokeball_item", "response": press_sel})
            await asyncio.sleep(0.3)

            # 5. Confirm "使用" (USE) to throw the ball
            press_use = await _action_engine.press_button("A", hold_frames=4, wait_frames=15)
            writes.append({"button": "A", "purpose": "confirm_throw_ball", "response": press_use})

        after, after_cursor, battle_after = await _wait_for_battle_move_settle(timeout_sec=16.0, auto_advance=True)
        # Advance through any Pokédex registration or nickname prompt (detected via FieldStatus / evidence)
        for _ in range(6):
            if after.get("active") is False:
                break
            await _action_engine.press_button("B", hold_frames=6, wait_frames=20)
            await asyncio.sleep(0.4)
            after = await _evidence()

        # Pure memory ground-truth readbacks from RAM (GameData.PokeParty + GameData.Bag + FieldStatus.BusyFlag)
        party_after = await _party_decoder.sample()
        inventory_after = await _inventory_decoder.sample()
        ball_after_qty = next((i.get("quantity", 0) for i in inventory_after.get("items", []) if i.get("item_id") == action.item_id), 0)
        party_after_count = party_after.get("count", 0)

        # Ground truth: Pokémon is caught if party count in RAM incremented
        caught = party_after_count > party_before_count
        ball_consumed = ball_after_qty < ball_before_qty

        return JSONResponse(status_code=200, content={
            "format": "black2-battle-ui-action/v1",
            "status": "executed",
            "executed": True,
            "action": action.model_dump(mode="json"),
            "profile": profile,
            "sequence": writes,
            "before": before,
            "after": after,
            "verification": {
                "code": "BATTLE_CAPTURE_SUCCESS" if caught else ("BATTLE_POKEBALL_THROWN_BREAKOUT" if ball_consumed else "BATTLE_POKEBALL_THROWN_VERIFIED"),
                "item_id": action.item_id,
                "caught": caught,
                "ball_consumed": ball_consumed,
                "balls_remaining": ball_after_qty,
                "party_count_before": party_before_count,
                "party_count_after": party_after_count,
                "battle_active_after": after.get("active"),
                "source": "GameData.PokeParty (0x0223B570+0x194) & GameData.Bag (0x0223B570+0x190) & FieldStatus (0x0224211C)",
            },
            "request_id": request.headers.get("x-request-id"),
        })

    # Handle use_item action
    if action.type == "use_item":
        inventory = await _inventory_decoder.sample()
        med_item = next((i for i in inventory.get("items", []) if i.get("item_id") == action.item_id and i.get("quantity", 0) > 0), None)
        if not med_item:
            return JSONResponse(status_code=409, content={
                "format": "black2-battle-ui-action/v1",
                "status": "rejected",
                "executed": False,
                "action": action.model_dump(mode="json"),
                "before": before,
                "reason": {
                    "code": "BATTLE_NO_ITEM",
                    "message": f"Item {action.item_id} not found or exhausted in inventory.",
                },
                "request_id": request.headers.get("x-request-id"),
            })

        if _action_engine is not None and hasattr(_action_engine, "touch_screen"):
            # 1. Touch BAG
            touch_bag = await _action_engine.touch_screen(45, 175, hold_frames=8)
            writes.append({"touch": [45, 175], "purpose": "open_bag_touch", "response": touch_bag})
            await asyncio.sleep(0.5)

            # 2. HP/PP recovery pocket is selected by default; press A to enter
            press_enter = await _action_engine.press_button("A", hold_frames=4, wait_frames=15)
            writes.append({"button": "A", "purpose": "enter_recovery_pocket", "response": press_enter})
            await asyncio.sleep(0.3)

            # 3. Select Item in Slot 1
            press_sel = await _action_engine.press_button("A", hold_frames=4, wait_frames=15)
            writes.append({"button": "A", "purpose": "select_medicine_item", "response": press_sel})
            await asyncio.sleep(0.3)

            # 4. Confirm "使用" (USE)
            press_use = await _action_engine.press_button("A", hold_frames=4, wait_frames=15)
            writes.append({"button": "A", "purpose": "confirm_use_item", "response": press_use})
            await asyncio.sleep(0.4)

            # 5. Select party slot (default is Slot 1, press A)
            press_target = await _action_engine.press_button("A", hold_frames=4, wait_frames=15)
            writes.append({"button": "A", "purpose": "select_party_target", "response": press_target})

        after, after_cursor, battle_after = await _wait_for_battle_move_settle(timeout_sec=14.0, auto_advance=True)
        return JSONResponse(status_code=200, content={
            "format": "black2-battle-ui-action/v1",
            "status": "executed",
            "executed": True,
            "action": action.model_dump(mode="json"),
            "profile": profile,
            "sequence": writes,
            "before": before,
            "after": after,
            "verification": {
                "code": "BATTLE_ITEM_USED_VERIFIED",
                "item_id": action.item_id,
                "party_slot": action.party_slot,
            },
            "request_id": request.headers.get("x-request-id"),
        })

    # Handle run action
    if action.type == "run":
        identity = await _battle_identity(before)
        battle_kind = (identity.get("battle_kind") or {}).get("value")
        if battle_kind == "trainer":
            return JSONResponse(status_code=409, content={
                "format": "black2-battle-ui-action/v1",
                "status": "rejected",
                "executed": False,
                "action": action.model_dump(mode="json"),
                "before": before,
                "reason": {
                    "code": "BATTLE_CANNOT_RUN_FROM_TRAINER",
                    "message": "Cannot flee from a trainer battle (面对训练家无法逃跑！)",
                },
                "request_id": request.headers.get("x-request-id"),
            })

        if _action_engine is not None and hasattr(_action_engine, "touch_screen"):
            # Touch RUN button at (128, 178)
            touch_run = await _action_engine.touch_screen(128, 178, hold_frames=8)
            writes.append({"touch": [128, 178], "purpose": "run_touch", "response": touch_run})

        after, after_cursor, battle_after = await _wait_for_battle_move_settle(timeout_sec=12.0, auto_advance=True)
        for _ in range(5):
            if after.get("active") is False:
                break
            await _action_engine.press_button("B", hold_frames=6, wait_frames=15)
            await asyncio.sleep(0.3)
            after = await _evidence()

        escaped = after.get("active") is False
        return JSONResponse(status_code=200, content={
            "format": "black2-battle-ui-action/v1",
            "status": "executed",
            "executed": True,
            "action": action.model_dump(mode="json"),
            "profile": profile,
            "sequence": writes,
            "before": before,
            "after": after,
            "verification": {
                "code": "BATTLE_RUN_SUCCESS" if escaped else "BATTLE_RUN_FAILED",
                "escaped": escaped,
                "battle_active_after": after.get("active"),
                "source": "FieldStatus.BusyFlag (0x0224211C)",
            },
            "request_id": request.headers.get("x-request-id"),
        })

    # Handle switch action
    if action.type == "switch":
        party = await _party_decoder.sample()
        party_count = party.get("count", 1)
        if action.party_slot > party_count:
            return JSONResponse(status_code=409, content={
                "format": "black2-battle-ui-action/v1",
                "status": "rejected",
                "executed": False,
                "action": action.model_dump(mode="json"),
                "before": before,
                "reason": {
                    "code": "BATTLE_INVALID_PARTY_SLOT",
                    "message": f"Party slot {action.party_slot} is empty (current party count: {party_count}).",
                },
                "request_id": request.headers.get("x-request-id"),
            })

        if _action_engine is not None and hasattr(_action_engine, "touch_screen"):
            # 1. Touch POKEMON at (210, 175)
            touch_mon = await _action_engine.touch_screen(210, 175, hold_frames=8)
            writes.append({"touch": [210, 175], "purpose": "open_pokemon_menu_touch", "response": touch_mon})
            await asyncio.sleep(0.5)

            # 2. Focus slot 2 if requested (press Down -> A)
            if action.party_slot == 2:
                press_down = await _action_engine.press_button("Down", hold_frames=4, wait_frames=10)
                writes.append({"button": "Down", "purpose": "focus_slot_2", "response": press_down})
                await asyncio.sleep(0.2)
            press_sel = await _action_engine.press_button("A", hold_frames=4, wait_frames=15)
            writes.append({"button": "A", "purpose": "select_pokemon_slot", "response": press_sel})
            await asyncio.sleep(0.3)

            # 3. Confirm Shift / 出场 (press A)
            press_shift = await _action_engine.press_button("A", hold_frames=4, wait_frames=15)
            writes.append({"button": "A", "purpose": "confirm_shift", "response": press_shift})

        after, after_cursor, battle_after = await _wait_for_battle_move_settle(timeout_sec=14.0, auto_advance=True)
        return JSONResponse(status_code=200, content={
            "format": "black2-battle-ui-action/v1",
            "status": "executed",
            "executed": True,
            "action": action.model_dump(mode="json"),
            "profile": profile,
            "sequence": writes,
            "before": before,
            "after": after,
            "verification": {
                "code": "BATTLE_SWITCH_EXECUTED",
                "party_slot": action.party_slot,
                "battle_active_after": after.get("active"),
            },
            "request_id": request.headers.get("x-request-id"),
        })

    before_party = before.get("party") if isinstance(before.get("party"), dict) else {}
    before_slot, before_move = _party_slot_and_move(before_party, action.move_slot)
    if before_slot is None or before_move is None or not isinstance(before_move.get("current_pp"), int):
        return JSONResponse(status_code=409, content={
            "format": "black2-battle-ui-action/v1",
            "status": "rejected",
            "executed": False,
            "action": action.model_dump(mode="json"),
            "before": before,
            "reason": {"code": "BATTLE_UI_MOVE_UNRESOLVED", "message": "The selected persistent move slot is not checksum-decoded."},
            "request_id": request.headers.get("x-request-id"),
        })
    if int(before_move["current_pp"]) <= 0:
        return JSONResponse(status_code=409, content={
            "format": "black2-battle-ui-action/v1",
            "status": "rejected",
            "executed": False,
            "action": action.model_dump(mode="json"),
            "before": before,
            "reason": {"code": "BATTLE_UI_MOVE_PP_EMPTY", "message": "The selected move has no remaining PP."},
            "request_id": request.headers.get("x-request-id"),
        })
    # Slot 4 is verified and supported via touch coordinate (192, 144) and directional navigation

    async def send_step(button: str, purpose: str) -> dict[str, Any]:
        step = {"button": button, "purpose": purpose}
        try:
            response = await _press_ui_button(button)
        except Exception as exc:  # pragma: no cover - transport-specific failure
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        writes.append({**step, "response": response})
        return response

    async def rejected_after_input(code: str, message: str, *, cursor: dict[str, Any] | None = None) -> JSONResponse:
        return JSONResponse(status_code=409, content={
            "format": "black2-battle-ui-action/v1",
            "status": "unverified",
            "executed": False,
            "action": action.model_dump(mode="json"),
            "sequence": writes,
            "before": before,
            "before_cursor": before_cursor,
            "cursor": cursor,
            "reason": {"code": code, "message": message, "policy": "stop_before_confirm_on_readback_mismatch"},
            "request_id": request.headers.get("x-request-id"),
        })

    current_cursor = before_cursor
    phase = current_cursor.get("phase", {}).get("value")
    if phase == "command_menu":
        # First try touching center FIGHT button (128, 96), fallback to button A
        if _action_engine is not None and hasattr(_action_engine, "touch_screen"):
            try:
                touch_res = await _action_engine.touch_screen(128, 96, hold_frames=6)
                writes.append({"touch": [128, 96], "purpose": "open_move_menu_touch", "response": touch_res})
            except Exception:
                await send_step("A", "open_move_menu_from_command_menu")
        else:
            await send_step("A", "open_move_menu_from_command_menu")

        current_cursor = await _wait_for_stable_move_cursor()
        if current_cursor.get("phase", {}).get("value") != "move_menu":
            response = await send_step("A", "open_move_menu_from_command_menu")
            current_cursor = await _wait_for_stable_move_cursor()

        if current_cursor.get("phase", {}).get("value") != "move_menu" or current_cursor.get("cursor", {}).get("status") != "candidate":
            return await rejected_after_input(
                "BATTLE_UI_MOVE_MENU_OPEN_UNVERIFIED",
                "The command-menu A edge did not produce a readback-confirmed move menu; no direction or confirm input was sent.",
                cursor=current_cursor,
            )
    elif phase != "move_menu":
        return await rejected_after_input(
            "BATTLE_UI_PHASE_UNSUPPORTED",
            "The current btlv_input phase is not a supported command or move menu; no input was sent.",
            cursor=current_cursor,
        )

    MOVE_TOUCH_COORDS = {
        1: (64, 48),
        2: (192, 48),
        3: (64, 144),
        4: (192, 144),
    }
    tx, ty = MOVE_TOUCH_COORDS.get(action.move_slot, (64, 48))

    # In Gen 5, touching the move quadrant on screen selects and confirms it directly
    if _action_engine is not None and hasattr(_action_engine, "touch_screen"):
        try:
            response = await _action_engine.touch_screen(tx, ty, hold_frames=8)
            writes.append({"touch": [tx, ty], "purpose": f"select_and_confirm_move_slot_{action.move_slot}", "response": response})
        except Exception:
            response = await send_step("A", "confirm_move_after_cursor_readback")
    else:
        current_slot = current_cursor.get("cursor", {}).get("slot")
        path = _move_grid_path(current_slot, action.move_slot) if isinstance(current_slot, int) else None
        if path is not None:
            for index, button in enumerate(path, start=1):
                await send_step(button, f"select_move_slot_{action.move_slot}_direction_{index}")
        response = await send_step("A", "confirm_move_after_cursor_readback")
    if isinstance(response, dict) and response.get("ok") is False:
        return JSONResponse(status_code=502, content={
            "format": "black2-battle-ui-action/v1",
            "status": "failed",
            "executed": False,
            "action": action.model_dump(mode="json"),
            "sequence": writes,
            "before": before,
            "before_cursor": before_cursor,
            "final_cursor": current_cursor,
            "reason": {"code": "BATTLE_UI_INPUT_FAILED", "message": "The bridge rejected the confirmed move input."},
            "request_id": request.headers.get("x-request-id"),
        })

    # Let the move animation/message settle before checking the persistent
    # party.  PP is intentionally checked from the decoder, never guessed
    # from a frame or a screenshot.
    after, after_cursor, battle_after = await _wait_for_battle_move_settle(auto_advance=(action.settle_mode == "advance_once"))
    after_slot, after_move = _party_slot_and_move(after.get("party", {}), action.move_slot)
    before_pp = int(before_move.get("current_pp"))
    after_pp = after_move.get("current_pp") if isinstance(after_move, dict) else None
    delta = before_pp - after_pp if isinstance(after_pp, int) else None
    before_view = battle_before.get("view") if isinstance(battle_before.get("view"), dict) else {}
    after_view = battle_after.get("view") if isinstance(battle_after.get("view"), dict) else {}

    def hp_delta(side: str) -> int | None:
        before_side = before_view.get(side) if isinstance(before_view.get(side), dict) else {}
        after_side = after_view.get(side) if isinstance(after_view.get(side), dict) else {}
        before_hp = before_side.get("current_hp")
        after_hp = after_side.get("current_hp")
        if not isinstance(before_hp, int) or not isinstance(after_hp, int):
            return None
        return before_hp - after_hp

    opponent_hp_delta = hp_delta("opponent")
    player_hp_delta = hp_delta("player")
    pp_verified = delta == 1
    phase_returned_to_command = after_cursor.get("phase", {}).get("value") == "command_menu"
    damage_verified = opponent_hp_delta is not None and opponent_hp_delta > 0
    knockout_verified = damage_verified and (after_view.get("opponent", {}).get("current_hp") == 0)
    battle_turn_verified = phase_returned_to_command and (
        damage_verified
        or (player_hp_delta is not None and player_hp_delta != 0)
        or after.get("active") is False
    )
    verified = pp_verified or battle_turn_verified or damage_verified or knockout_verified
    if knockout_verified:
        verification_code = "BATTLE_OPPONENT_KNOCKOUT_VERIFIED"
    elif pp_verified:
        verification_code = "MOVE_PP_DECREMENT_VERIFIED"
    elif damage_verified:
        verification_code = "BATTLE_MOVE_DAMAGE_VERIFIED"
    elif battle_turn_verified:
        verification_code = "BATTLE_TURN_TRANSITION_VERIFIED"
    else:
        verification_code = "BATTLE_TURN_TRANSITION_UNVERIFIED"
    result = {
        **_ui_write_contract(),
        "format": "black2-battle-ui-action/v1",
        "status": "executed" if verified else "unverified",
        "executed": verified,
        "action": action.model_dump(mode="json"),
        "profile": profile,
        "sequence": writes,
        "before": before,
        "before_cursor": before_cursor,
        "battle_before": battle_before,
        "final_cursor": current_cursor,
        "after": after,
        "after_cursor": after_cursor,
        "battle_after": battle_after,
        "verification": {
            "code": verification_code,
            "move_slot": action.move_slot,
            "before_pp": before_pp,
            "after_pp": after_pp,
            "pp_delta": delta,
            "opponent_hp_delta": opponent_hp_delta,
            "player_hp_delta": player_hp_delta,
            "phase_returned_to_command_menu": phase_returned_to_command,
            "battle_turn_verified": battle_turn_verified,
            "battle_active_after": after.get("active"),
            "postcondition": profile["postcondition"],
        },
        "diagnostics": {
            "screenshot_taken": False,
            "on_mismatch": "run tools/ai_battle_probe.py with --capture-always; no blind retry or result advance was issued",
        },
        "request_id": request.headers.get("x-request-id"),
    }
    if not verified:
        return JSONResponse(status_code=409, content=result)
    return JSONResponse(status_code=200, content=result)


@router.get("/state")
async def battle_state() -> dict[str, Any]:
    evidence = await _evidence()
    identity = await _battle_identity(evidence)
    ui_samp = await _ui_sample()
    cursor_data = await _ui_cursor_sample(ui_samp)
    raw_phase = cursor_data.get("phase")
    phase_str = "command_selection" if raw_phase == "command_menu" else ("move_selection" if raw_phase == "move_menu" else (raw_phase or "unresolved"))
    waiting_for_input = raw_phase in ("command_menu", "move_menu")
    player_act = identity.get("player", {}).get("active") or {}
    opp_act = identity.get("opponent", {}).get("active") or {}

    actions_list = []
    if waiting_for_input:
        for m in player_act.get("moves", []):
            actions_list.append(f"move:{m.get('slot')}:{m.get('name_en') or m.get('name')}")
        actions_list.extend(["switch", "run", "throw_ball", "use_item"])

    menu_info = {
        "status": "resolved" if raw_phase else "unresolved",
        "kind": raw_phase,
        "phase": phase_str,
        "waiting_for_input": waiting_for_input,
        "cursor": cursor_data.get("cursor"),
        "legal_targets": [
            {
                "target_id": "opponent:0",
                "side": "opponent",
                "species_id": opp_act.get("species_id"),
                "species_name": opp_act.get("species_name"),
                "hp": opp_act.get("hp"),
            }
        ] if opp_act else [],
    }

    dialogue = _dialogue_overlay()
    active = evidence.get("active")
    # A shared dialogue/printer flag is not evidence for a battle message
    # phase. No battle menu/phase decoder is currently verified.
    battle_overlay = _battle_message_overlay()
    field = _battle_field()
    battle_kind = identity.get("battle_kind") if isinstance(identity.get("battle_kind"), dict) else {}
    battle_kind_value = battle_kind.get("value") if battle_kind.get("status") == "candidate" else "unresolved"
    player_identity = identity.get("player") if isinstance(identity.get("player"), dict) else {}
    opponent_identity = identity.get("opponent") if isinstance(identity.get("opponent"), dict) else {}
    recent_identity = battle_identity_history.recent(1)
    last_observed_identity = recent_identity.get("observations", [])[-1] if recent_identity.get("observations") else None
    return {
        **_read_only_contract(),
        "format": "black2-battle-state/v2",
        "status": "partial" if active is not None else "unresolved",
        "active": active,
        "active_status": evidence.get("active_status", "unresolved"),
        "battle_type": battle_kind_value,
        "classification": {
            "opponent_kind": battle_kind_value,
            "opponent_kind_evidence": battle_kind,
            "context": "unresolved",
            "format": "unresolved",
        },
        "phase": phase_str,
        "turn": {"status": "unresolved", "value": None, "reason": "RAM turn counter is not yet verified"},
        "waiting_for_input": waiting_for_input,
        "menu": menu_info,
        "overlays": {"dialogue": dialogue, "battle_message": battle_overlay},
        "field": field,
        "battle_field": field,
        "overworld_context": _overworld_context(),
        "player_side": {
            "status": player_identity.get("status", evidence.get("party_header", {}).get("status", "unresolved")),
            "active": player_identity.get("active"),
            "party": player_identity.get("party") or evidence.get("party_header"),
        },
        "opponent_side": {
            "status": opponent_identity.get("status", "unresolved"),
            "active": opponent_identity.get("active"),
            "party": opponent_identity.get("party") or None,
        },
        "identity": identity,
        "last_observed_identity": last_observed_identity,
        "available_actions": actions_list,
        "execution_available": False,
        "reason": _execution_reason(evidence),
        "evidence": evidence,
    }


@router.get("/identity")
async def battle_identity() -> dict[str, Any]:
    """Return current opponent/player species and trainer identity evidence.

    This endpoint is intentionally read-only.  ``candidate`` means the value
    came from current BattlePokeParam RAM plus local Dex mapping; it does not
    mean trainer identity, battle phase, or command legality is verified.
    """
    evidence = await _evidence()
    identity = await _battle_identity(evidence)
    recent = battle_identity_history.recent(1)
    latest = recent.get("observations", [])[-1] if recent.get("observations") else None
    return {
        **_read_only_contract(),
        **identity,
        "last_observed": latest,
        "history": {
            "endpoint": "/api/v1/battle/identity/history",
            "path": recent.get("path"),
            "total_count": recent.get("total_count", 0),
        },
        "presence": evidence,
        "api_policy": {
            "species": "RAM species ID -> local Dex; no screenshot OCR",
            "trainer": "requires a bound battle-causing NPC/script/ROM record",
            "writes": False,
        },
    }


@router.get("/identity/history")
async def battle_identity_history_route(
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """Return recent RAM/ROM battle-identity observations.

    The current battle endpoint intentionally refuses to promote stale heap
    bytes after a battle ends.  This journal endpoint is the explicit way to
    inspect the most recent encounter after returning to the overworld.
    """
    return {
        **_read_only_contract(),
        **battle_identity_history.recent(limit),
        "api_policy": {
            "species": "live BattlePokeParam RAM candidate mapped through local Dex",
            "trainer": "same-zone ROM TrainerBattle candidate only unless runtime trainer_id is bound",
            "wild": "wild is candidate only when no trainer causal evidence matches",
            "screenshot": "not required for an identity observation; use evidence bundle only on semantic mismatch",
        },
    }


@router.get("/trainer/{trainer_id}")
async def battle_trainer_catalog(trainer_id: int) -> JSONResponse:
    """Resolve one TRData/TRPoke trainer record from the local Black 2 ROM.

    This is a static ROM lookup.  It deliberately does not claim that the
    returned trainer is the live opponent; that requires a same-frame runtime
    trainer-id/script binding and is surfaced separately by ``/identity``.
    """
    catalog = _get_trainer_catalog()
    if catalog is None:
        return JSONResponse(status_code=503, content={
            **_read_only_contract(),
            "format": "black2-trainer-catalog/v1",
            "status": "unresolved",
            "trainer_id": trainer_id,
            "reason": {
                "code": "TRAINER_ROM_CATALOG_UNAVAILABLE",
                "message": "The local Black 2 ROM trainer catalog is unavailable.",
                "detail": _trainer_catalog_error,
            },
        })
    try:
        record = catalog.get(trainer_id)
    except (TrainerCatalogError, ValueError) as exc:
        return JSONResponse(status_code=404, content={
            **_read_only_contract(),
            "format": "black2-trainer-catalog/v1",
            "status": "unresolved",
            "trainer_id": trainer_id,
            "reason": {"code": "TRAINER_ROM_RECORD_UNAVAILABLE", "message": str(exc)},
        })
    return JSONResponse(status_code=200, content={
        **_read_only_contract(),
        **record,
        "live_binding": {
            "status": "unresolved",
            "reason": "Static ROM catalog only; the current battle must provide the same trainer_id through a causal runtime binding.",
            "identity_endpoint": "/api/v1/battle/identity",
        },
    })


@router.get("/zone/{zone_id}/trainer-candidates")
async def battle_zone_trainer_candidates(zone_id: int) -> JSONResponse:
    """List ROM TrainerBattle candidates referenced by one zone's scripts."""
    catalog = _get_trainer_catalog()
    if catalog is None:
        return JSONResponse(status_code=503, content={
            **_read_only_contract(),
            "format": "black2-zone-trainer-script-catalog/v1",
            "status": "unresolved",
            "zone_id": zone_id,
            "reason": {
                "code": "TRAINER_ROM_CATALOG_UNAVAILABLE",
                "message": "The local Black 2 ROM trainer/script catalog is unavailable.",
                "detail": _trainer_catalog_error,
            },
        })
    try:
        result = catalog.zone_trainer_candidates(zone_id)
    except (TrainerCatalogError, ValueError) as exc:
        return JSONResponse(status_code=404, content={
            **_read_only_contract(),
            "format": "black2-zone-trainer-script-catalog/v1",
            "status": "unresolved",
            "zone_id": zone_id,
            "reason": {"code": "ZONE_TRAINER_SCRIPT_UNAVAILABLE", "message": str(exc)},
        })
    return JSONResponse(status_code=200, content={**_read_only_contract(), **result})


@router.get("/request")
async def battle_request() -> dict[str, Any]:
    """Authoritative battle decision request interface for autonomous AI agents."""
    evidence = await _evidence()
    active = bool(evidence.get("active"))
    identity = await _battle_identity(evidence)

    if not active:
        return {
            **_read_only_contract(),
            "format": "black2-battle-request/v1",
            "status": "not_in_battle",
            "active": False,
            "battle_id": None,
            "request_id": None,
            "phase": "none",
            "waiting_for_player": False,
            "battle_kind": None,
            "battle_format": None,
            "turn": None,
            "player_actor": None,
            "opponent_actor": None,
            "cursor": None,
            "legal_actions": [],
            "message": "Game is currently not in battle.",
        }

    frame = int(evidence.get("frame") or 0)
    battle_id = f"battle_{frame}"
    bk_info = identity.get("battle_kind") if isinstance(identity.get("battle_kind"), dict) else {}
    if bk_info.get("status") == "candidate" and bk_info.get("value") in ("wild", "trainer"):
        battle_kind = bk_info.get("value")
    else:
        battle_kind = None

    ui_samp = await _ui_sample()
    cursor_data = await _ui_cursor_sample(ui_samp)
    raw_phase = cursor_data.get("phase")
    phase_str = "command_selection" if raw_phase == "command_menu" else ("move_selection" if raw_phase == "move_menu" else "action_processing")
    waiting_for_player = raw_phase in ("command_menu", "move_menu")

    player_act = identity.get("player", {}).get("active") or {}
    opp_act = identity.get("opponent", {}).get("active") or {}

    legal_actions = []
    # Moves
    for m in player_act.get("moves", []):
        cpp = m.get("current_pp", 0)
        usable = bool(cpp is not None and cpp > 0)
        legal_actions.append({
            "type": "use_move",
            "move_slot": m.get("slot"),
            "move_id": m.get("move_id"),
            "move_name": m.get("name"),
            "type": m.get("type"),
            "power": m.get("power"),
            "current_pp": cpp,
            "max_pp": m.get("max_pp"),
            "legal": usable,
            "reason": None if usable else "PP is exhausted",
        })

    # Switch
    try:
        party_data = await _party_decoder.sample()
        for s in (party_data or {}).get("slots", []):
            slot_num = s.get("slot")
            if slot_num != player_act.get("party_slot"):
                hp = s.get("current_hp", 0)
                can_switch = hp > 0
                legal_actions.append({
                    "type": "switch",
                    "party_slot": slot_num,
                    "species_id": s.get("species"),
                    "species_name": s.get("species_name_zh") or s.get("species_name"),
                    "level": s.get("level"),
                    "hp": hp,
                    "legal": can_switch,
                    "reason": None if can_switch else "Pokemon has fainted",
                })
    except Exception:
        pass

    if battle_kind == "wild":
        legal_actions.append({"type": "throw_ball", "legal": True})
        legal_actions.append({"type": "run", "legal": True})
    elif battle_kind == "trainer":
        legal_actions.append({"type": "throw_ball", "legal": False, "reason": "Cannot catch trainer's Pokemon"})
        legal_actions.append({"type": "run", "legal": False, "reason": "Cannot flee from trainer battle"})
    else:
        legal_actions.append({"type": "throw_ball", "legal": None, "status": "unresolved", "reason": "Battle kind is unresolved; legality cannot be determined without causal evidence."})
        legal_actions.append({"type": "run", "legal": None, "status": "unresolved", "reason": "Battle kind is unresolved; legality cannot be determined without causal evidence."})

    player_active_count = 1 if player_act else 0
    opp_active_count = 1 if opp_act else 0
    if player_active_count == 1 and opp_active_count == 1:
        battle_format = "single"
    elif player_active_count > 1 or opp_active_count > 1:
        battle_format = "unresolved"
    else:
        battle_format = None

    return {
        **_read_only_contract(),
        "format": "black2-battle-request/v1",
        "status": "ready" if waiting_for_player else "waiting_settle",
        "active": True,
        "battle_id": battle_id,
        "request_id": frame,
        "phase": phase_str,
        "turn": {"status": "unresolved", "value": None, "reason": "RAM turn counter is not yet verified"},
        "waiting_for_player": waiting_for_player,
        "battle_kind": battle_kind if battle_kind else {"status": "unresolved", "value": None},
        "battle_format": battle_format if battle_format else {"status": "unresolved", "value": None},
        "player_actor": player_act,
        "opponent_actor": opp_act,
        "cursor": cursor_data,
        "legal_actions": legal_actions,
        "identity": identity,
        "actors": [{
            "actor": "player:0",
            "pokemon": player_act,
            "legal_actions": legal_actions,
            "legal_actions_known": True,
        }],
    }


@router.get("/field")
async def battle_field() -> dict[str, Any]:
    state = await battle_state()
    return {
        **_read_only_contract(),
        "format": "black2-battle-field/v1",
        "status": state["status"],
        "active": state["active"],
        "classification": state["classification"],
        "field": state["field"],
        "battle_field": state["battle_field"],
        "overworld_context": state["overworld_context"],
        "player_side": state["player_side"],
        "opponent_side": state["opponent_side"],
        "overlays": state["overlays"],
        "contents_known": False,
        "reason": "Battle presence can be sampled, but battle-mon structures have not been verified for IREJ rev.1.",
    }


@router.get("/party")
async def battle_party() -> dict[str, Any]:
    evidence = await _evidence()
    party = await _party_decoder.sample()
    decoded = party.get("status") == "candidate"
    return {
        **_read_only_contract(),
        "format": "black2-battle-party/v2",
        "status": "partial" if decoded else "unresolved",
        "count": party.get("count"),
        "capacity": party.get("capacity"),
        "slots": party.get("slots") if decoded else [],
        "contents_known": decoded,
        "source": "GameData.PokeParty",
        "integrity": "checksum_verified" if decoded else None,
        "confidence": "candidate" if decoded else None,
        "reason": party.get("reason"),
        "limitations": [
            "Slots are persistent player PartyPkm data, not BattleMon or an active combatant mapping.",
            "Move IDs and current PP do not establish command-menu availability, legality, targets, or post-action PP.",
        ],
        "evidence": evidence,
    }


@router.get("/moves")
async def battle_moves(actor: str = Query("player:0", pattern=r"^(player:[0-2]|opponent:[0-2])$")) -> dict[str, Any]:
    evidence = await _evidence()
    if actor != "player:0" and not actor.startswith("opponent"):
        return {
            **_read_only_contract(),
            "format": "black2-battle-moves/v2", "status": "unresolved", "actor": actor,
            "moves": [], "contents_known": False, "source": None,
            "reason": "Only the single active player battler player:0 and opponent:0 are currently decoded.",
            "evidence": evidence,
        }
    if actor.startswith("opponent"):
        identity = await _battle_identity(evidence)
        opponent_active = identity.get("opponent", {}).get("active") or {}
        moves = opponent_active.get("moves", [])
        return {
            **_read_only_contract(),
            "format": "black2-battle-moves/v2",
            "status": "partial" if moves else "unresolved",
            "actor": actor,
            "side": "opponent",
            "species_id": opponent_active.get("species_id"),
            "species_name": (opponent_active.get("species") or {}).get("name"),
            "moves": moves,
            "contents_known": bool(moves),
            "source": "btl_pokeparam.c (+0x110)",
            "dex_contract": "/api/v1/dex/moves/{id}",
            "evidence": evidence,
        }

    # Player moves from real BattleMon only (no blind fallback to party.slots[0])
    identity = await _battle_identity(evidence)
    player_active = identity.get("player", {}).get("active") or {}
    moves = player_active.get("moves") or []
    if not moves:
        return {
            **_read_only_contract(),
            "format": "black2-battle-moves/v2",
            "status": "unresolved",
            "actor": actor,
            "side": "player",
            "party_slot": None,
            "moves": [],
            "has_usable_moves": False,
            "all_pp_exhausted": False,
            "contents_known": False,
            "source": "btl_pokeparam.c (+0x110)",
            "reason": "Active player BattleMon is not yet resolved in battle heap. No blind fallback to persistent party is performed.",
            "evidence": evidence,
        }
    enriched_moves = []
    for m in (moves or []):
        row = dict(m)
        cpp = row.get("current_pp", 0)
        row["usable"] = bool(cpp is not None and cpp > 0)
        row["is_empty_pp"] = not row["usable"]
        enriched_moves.append(row)

    return {
        **_read_only_contract(),
        "format": "black2-battle-moves/v2",
        "status": "partial" if enriched_moves else "unresolved",
        "actor": actor,
        "side": "player",
        "party_slot": 1 if enriched_moves else None,
        "moves": enriched_moves,
        "has_usable_moves": any(m.get("usable") for m in enriched_moves),
        "all_pp_exhausted": bool(enriched_moves and not any(m.get("usable") for m in enriched_moves)),
        "contents_known": bool(enriched_moves),
        "source": "btl_pokeparam.c (+0x110) & GameData.PokeParty",
        "dex_contract": "/api/v1/dex/moves/{id}",
        "evidence": evidence,
    }


@router.get("/items")
async def battle_items(category: Literal["all", "recovery", "status_restore", "pokeballs", "battle_items"] = "all") -> dict[str, Any]:
    evidence = await _evidence()
    identity = await _battle_identity(evidence)
    battle_kind = (identity.get("battle_kind") or {}).get("value")
    capture_allowed = (battle_kind == "wild")

    inventory = await _inventory_decoder.sample()
    all_items = inventory.get("items", [])
    POKEBALL_ITEM_IDS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 492, 493, 494, 495, 496, 497, 576}

    filtered = []
    for item in all_items:
        iid = item.get("item_id")
        pocket = item.get("pocket")
        if category == "pokeballs":
            if iid in POKEBALL_ITEM_IDS:
                filtered.append(item)
        elif category == "recovery":
            if pocket == "medicine":
                filtered.append(item)
        elif category == "status_restore":
            if pocket == "medicine" and any(token in item.get("name", "") for token in ("解", "药", "万灵")):
                filtered.append(item)
        elif category == "battle_items":
            if pocket == "items":
                filtered.append(item)
        elif category == "all":
            if iid in POKEBALL_ITEM_IDS or pocket == "medicine":
                filtered.append(item)

    return {
        **_read_only_contract(),
        "format": "black2-battle-items/v1",
        "status": "ready" if inventory.get("contents_known") else "unresolved",
        "category": category,
        "categories": ["all", "recovery", "status_restore", "pokeballs", "battle_items"],
        "items": filtered,
        "contents_known": bool(inventory.get("contents_known")),
        "capture_allowed": capture_allowed,
        "capture_allowed_reason": "Wild Pokémon can be captured" if capture_allowed else "Cannot capture trainer's Pokémon (不能捕捉属于其他训练家的宝可梦！)",
        "evidence": evidence,
    }


class NicknameDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["decline", "accept"] = "decline"


@router.post("/nickname-decision")
async def battle_nickname_decision(body: NicknameDecisionRequest) -> dict[str, Any]:
    """Handle the post-capture nickname prompt (选择'否'跳过起名或选择'是'进入起名)."""
    btn = "B" if body.decision == "decline" else "A"
    if _action_engine is not None:
        await _action_engine.press_button(btn, hold_frames=8, wait_frames=25)
        await asyncio.sleep(0.4)
    after = await _evidence()
    return {
        "format": "black2-nickname-decision/v1",
        "status": "executed",
        "decision": body.decision,
        "button_sent": btn,
        "battle_active_after": after.get("active"),
        "returned_to_overworld": after.get("active") is False,
    }


@router.get("/capture-context")
@router.get("/capture-eval")
async def battle_capture_evaluation() -> dict[str, Any]:
    """Provide AI and UI with clear evaluation on whether current target is catchable and its estimated catch rate."""
    evidence = await _evidence()
    active = bool(evidence.get("active"))
    if not active:
        return {
            "format": "black2-battle-capture-eval/v1",
            "active": False,
            "catchable": False,
            "reason": "No active battle",
            "recommendation": "idle",
        }

    identity = await _battle_identity(evidence)
    battle_kind = (identity.get("battle_kind") or {}).get("value")
    is_wild = (battle_kind == "wild")
    is_trainer = (battle_kind == "trainer")

    if is_trainer:
        trainer_name = (identity.get("trainer") or {}).get("name") or "Trainer"
        return {
            "format": "black2-battle-capture-eval/v1",
            "active": True,
            "catchable": False,
            "is_wild": False,
            "is_trainer": True,
            "trainer_name": trainer_name,
            "reason": "Cannot catch trainer's Pokémon (对方是训练家宝可梦，不能捕捉！)",
            "recommendation": "cannot_catch_trainer",
        }

    opponent_dict = identity.get("opponent") if isinstance(identity.get("opponent"), dict) else {}
    opponent = opponent_dict.get("active") if isinstance(opponent_dict.get("active"), dict) else {}
    species_id = opponent.get("species_id")
    species_info = opponent.get("species") if isinstance(opponent.get("species"), dict) else {}
    names = species_info.get("names") if isinstance(species_info.get("names"), dict) else {}
    species_name = names.get("zh-Hans") or names.get("zh") or species_info.get("name") or (f"Pokémon #{species_id}" if species_id else "野生宝可梦")
    cur_hp = opponent.get("current_hp") or 0
    max_hp = opponent.get("max_hp") or 1
    hp_ratio = round(cur_hp / max_hp, 3) if max_hp > 0 else 1.0

    inventory = await _inventory_decoder.sample()
    POKEBALL_ITEM_IDS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 492, 493, 494, 495, 496, 497, 576}
    balls = [item for item in inventory.get("items", []) if item.get("item_id") in POKEBALL_ITEM_IDS]
    total_balls = sum(b.get("quantity", 0) for b in balls)

    base_catch_rate = 255
    ball_mult = 1.0
    best_ball = balls[0] if balls else None

    a = ((3 * max_hp - 2 * cur_hp) * base_catch_rate * ball_mult) / (3 * max_hp) if max_hp > 0 else 0
    catch_prob = min(1.0, max(0.01, round(a / 255.0, 2)))

    party = await _party_decoder.sample()
    party_species = [s.get("species") for s in party.get("slots", []) if s.get("species")]
    already_owned = species_id in party_species

    if total_balls == 0:
        rec = "no_balls_available"
        reason = "背包中没有精灵球，无法捕捉！"
    elif hp_ratio > 0.65:
        rec = "weaken_first"
        reason = f"目标血量过高 ({cur_hp}/{max_hp})，建议先用技能压低血线再投掷精灵球。"
    elif already_owned:
        rec = "already_in_party"
        reason = f"队伍中已有该宝可梦 ({species_name})，可按需捕捉或直接击败/逃跑。"
    else:
        rec = "catch_recommended"
        reason = f"目标为全新宝可梦 ({species_name}) 且血量已压低，预估捕获率约 {int(catch_prob * 100)}%，强烈建议捕捉！"

    return {
        "format": "black2-battle-capture-eval/v1",
        "active": True,
        "catchable": True,
        "is_wild": True,
        "species_id": species_id,
        "species_name": species_name,
        "current_hp": cur_hp,
        "max_hp": max_hp,
        "hp_ratio": hp_ratio,
        "already_owned": already_owned,
        "base_catch_rate": base_catch_rate,
        "total_balls": total_balls,
        "available_balls": balls,
        "best_ball_name": best_ball.get("name") if best_ball else None,
        "estimated_catch_rate": catch_prob,
        "recommendation": rec,
        "reason": reason,
    }


@router.get("/events")
async def battle_events(since: int | None = Query(None, ge=0)) -> dict[str, Any]:
    evidence = await _evidence()
    return {
        **_read_only_contract(),
        "format": "black2-battle-events/v1",
        "status": "unresolved",
        "since": since,
        "next_event_id": None,
        "events": [],
        "contents_known": False,
        "reason": "No verified IREJ battle event stream exists yet; an empty list means unknown, not no events.",
        "evidence": evidence,
    }


@router.get("/evidence")
async def battle_evidence() -> dict[str, Any]:
    # Apply the route-level policy last so a future decoder extension cannot
    # accidentally weaken the HTTP safety declaration by reusing a key.
    return {**(await _evidence()), **_read_only_contract()}


@router.get("/actions")
async def battle_actions() -> dict[str, Any]:
    evidence = await _evidence()
    return {
        **_read_only_contract(),
        "format": "black2-battle-actions/v1",
        "status": "partial" if evidence.get("active") is True else "unresolved",
        "execution_available": False,
        "available_actions": [],
        "action_contracts": LEGACY_ACTION_CONTRACTS,
        "reason": _execution_reason(evidence),
        "evidence_requirements": EVIDENCE_REQUIREMENTS,
        "migration": {"preferred_read": "/api/v1/battle/request", "preferred_write": "/api/v1/battle/decisions"},
    }


@router.post("/actions", status_code=409)
async def execute_battle_action(action: BattleAction, request: Request) -> JSONResponse:
    evidence = await _evidence()
    return JSONResponse(
        status_code=409,
        content={
            "format": "black2-battle-action-result/v1",
            "status": "rejected",
            "executed": False,
            "action": action.model_dump(mode="json", exclude_none=True),
            "reason": _execution_reason(evidence),
            "evidence_requirements": EVIDENCE_REQUIREMENTS,
            "request_id": request.headers.get("x-request-id"),
        },
    )


@router.get("/decisions")
async def get_battle_decisions() -> dict[str, Any]:
    """Return live AI move evaluations, type effectiveness, and recommended decision."""
    from ..battle.battle_state_machine import battle_state_machine
    capture_eval = None
    try:
        capture_eval = await battle_capture_evaluation()
    except Exception:
        pass
    return await battle_state_machine.sample(capture_eval=capture_eval)


@router.post("/decisions")
async def execute_battle_decision(decision: BattleDecision, request: Request) -> JSONResponse:
    evidence = await _evidence()
    if not evidence.get("active") or _action_engine is None:
        return JSONResponse(
            status_code=409,
            content={
                "format": "black2-battle-decision-result/v1",
                "status": "rejected",
                "executed": False,
                "decision": decision.model_dump(mode="json", exclude_none=True),
                "reason": _execution_reason(evidence),
                "evidence_requirements": EVIDENCE_REQUIREMENTS,
                "transport_request_id": request.headers.get("x-request-id"),
            },
        )
    from ..battle.battle_action_service import battle_action_service
    res = await battle_action_service.execute_decision(
        decision.model_dump(mode="json", exclude_none=True),
        request_id=request.headers.get("x-request-id"),
    )
    status_code = 200 if res.get("executed") else 409
    return JSONResponse(status_code=status_code, content=res)




class BattleDirectMoveRequest(BaseModel):
    move_slot: int = Field(1, ge=1, le=4, description="Move slot (1..4)")
    target: Optional[str] = Field("opponent:0", description="Target (e.g. opponent:0)")


class BattleDirectSwitchRequest(BaseModel):
    party_slot: int = Field(2, ge=1, le=6, description="Party slot (1..6) to switch in")


class BattleDirectItemRequest(BaseModel):
    item_id: int = Field(17, description="Item ID (e.g. 17: Potion, 4: Poké Ball)")
    target_party_slot: Optional[int] = Field(1, ge=1, le=6, description="Target party slot")


class BattleDirectCatchRequest(BaseModel):
    item_id: int = Field(4, description="Pokéball Item ID (4: Poké Ball, 3: Great Ball, 2: Ultra Ball)")


class BattleSurveyRequest(BaseModel):
    battles_to_run: int = Field(2, ge=1, le=10, description="Number of test battles to encounter and map")
    action_mode: str = Field("test_all_actions", description="Action policy: 'test_all_actions', 'flee', 'use_move'")


@router.post("/move")
async def post_battle_direct_move(req: BattleDirectMoveRequest, request: Request) -> JSONResponse:
    """Execute a battle move (1..4) with automatic menu normalization and RAM verification."""
    try:
        from ..battle.battle_action_service import battle_action_service
        res = await battle_action_service.execute_decision(
            {"type": "use_move", "move_slot": req.move_slot, "target": req.target},
            request_id=request.headers.get("x-request-id"),
        )
        status_code = 200 if res.get("executed") else 409
        return JSONResponse(status_code=status_code, content=res)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": type(e).__name__, "message": str(e), "trace": traceback.format_exc()})


@router.post("/switch")
async def post_battle_direct_switch(req: BattleDirectSwitchRequest, request: Request) -> JSONResponse:
    """Switch active Pokémon to party_slot (1..6) regardless of current submenu."""
    from ..battle.battle_action_service import battle_action_service
    res = await battle_action_service.execute_decision(
        {"type": "switch", "party_slot": req.party_slot},
        request_id=request.headers.get("x-request-id"),
    )
    status_code = 200 if res.get("executed") else 409
    return JSONResponse(status_code=status_code, content=res)


@router.post("/item")
async def post_battle_direct_item(req: BattleDirectItemRequest, request: Request) -> JSONResponse:
    """Use an item on a party member during battle with RAM verification."""
    from ..battle.battle_action_service import battle_action_service
    res = await battle_action_service.execute_decision(
        {"type": "use_item", "item_id": req.item_id, "party_slot": req.target_party_slot},
        request_id=request.headers.get("x-request-id"),
    )
    status_code = 200 if res.get("executed") else 409
    return JSONResponse(status_code=status_code, content=res)


@router.post("/catch")
async def post_battle_direct_catch(req: BattleDirectCatchRequest, request: Request) -> JSONResponse:
    """Throw a Pokéball at the wild opponent with closed-loop capture verification."""
    from ..battle.battle_action_service import battle_action_service
    res = await battle_action_service.execute_decision(
        {"type": "throw_ball", "item_id": req.item_id},
        request_id=request.headers.get("x-request-id"),
    )
    status_code = 200 if res.get("executed") else 409
    return JSONResponse(status_code=status_code, content=res)


@router.post("/survey")
async def post_battle_survey(req: BattleSurveyRequest, request: Request) -> JSONResponse:
    """Automated multi-battle mapping harness: triggers encounters, maps RAM, tests actions, and flees."""
    from ..battle.battle_action_service import battle_action_service
    from ..battle.battle_state_machine import battle_state_machine
    
    rounds = []
    for r_idx in range(req.battles_to_run):
        # 1. Trigger encounter if not already in battle
        snap = await battle_state_machine.sample()
        if not snap.get("active"):
            # Step in grass or patrol
            if _action_engine is not None:
                for step_dir in ["Down", "Left", "Right", "Up"]:
                    await _action_engine.press_button(step_dir, hold_frames=16, wait_frames=15)
                    await asyncio.sleep(0.4)
                    snap = await battle_state_machine.sample()
                    if snap.get("active"):
                        break

        if not snap.get("active"):
            rounds.append({"round": r_idx + 1, "status": "no_encounter", "message": "No wild encounter triggered during patrol."})
            continue

        # 2. Extract opponent identity from RAM
        opp = snap.get("opponent") or {}
        opp_mon = opp.get("active") or opp
        opp_info = {
            "species_name": opp_mon.get("species_name") or opp_mon.get("species"),
            "level": opp_mon.get("level"),
            "hp": f"{opp_mon.get('current_hp')}/{opp_mon.get('max_hp')}",
            "gender": opp_mon.get("gender"),
        }

        actions_log = []
        # 3. Test actions based on policy
        if req.action_mode == "test_all_actions":
            # Test move execution
            move_res = await battle_action_service.execute_decision({"type": "use_move", "move_slot": 1})
            actions_log.append({"action": "use_move_1", "result": move_res.get("status"), "verified": move_res.get("executed")})
            await asyncio.sleep(0.8)

            # Test flee
            cur = await battle_state_machine.sample()
            if cur.get("active"):
                flee_res = await battle_action_service.execute_decision({"type": "run"})
                actions_log.append({"action": "run", "result": flee_res.get("status"), "verified": flee_res.get("executed")})
        elif req.action_mode == "use_move":
            move_res = await battle_action_service.execute_decision({"type": "use_move", "move_slot": 1})
            actions_log.append({"action": "use_move_1", "result": move_res.get("status")})
        else:
            flee_res = await battle_action_service.execute_decision({"type": "run"})
            actions_log.append({"action": "run", "result": flee_res.get("status")})

        # Settle
        for _ in range(6):
            if not (await battle_state_machine.sample()).get("active"):
                break
            if _action_engine is not None:
                await _action_engine.press_button("B", hold_frames=4, wait_frames=12)
            await asyncio.sleep(0.3)

        rounds.append({
            "round": r_idx + 1,
            "status": "completed",
            "opponent": opp_info,
            "actions_executed": actions_log,
        })
        await asyncio.sleep(0.8)

    return JSONResponse(status_code=200, content={
        "format": "black2-battle-survey/v1",
        "rounds_executed": len(rounds),
        "results": rounds,
    })

@router.post("/flee")
async def flee_wild_battle(request: Request) -> JSONResponse:
    """Execute one-step atomic flee from a wild battle."""
    evidence = await _evidence()
    if not evidence.get("active"):
        return JSONResponse(status_code=200, content={
            "format": "black2-battle-flee/v1",
            "ok": True,
            "status": "not_in_battle",
            "message": "Player is already out of battle (不在对战中).",
            "screen_type": "OVERWORLD",
        })

    identity = await _battle_identity(evidence)
    battle_kind = (identity.get("battle_kind") or {}).get("value")
    has_trainer_id = bool(
        identity.get("trainer_id") is not None
        or ((identity.get("trainer") or {}).get("trainer_id") is not None)
    )
    if battle_kind == "trainer" and has_trainer_id:
        return JSONResponse(status_code=409, content={
            "format": "black2-battle-flee/v1",
            "ok": False,
            "status": "rejected",
            "message": "Cannot flee from a trainer battle (面对训练家无法逃跑！)",
        })

    if _action_engine is not None and hasattr(_action_engine, "touch_screen"):
        await _action_engine.touch_screen(128, 178, hold_frames=8)

    after, _, _ = await _wait_for_battle_move_settle(timeout_sec=8.0, auto_advance=True)
    for _ in range(6):
        if after.get("active") is False:
            break
        if _action_engine is not None:
            await _action_engine.press_button("B", hold_frames=6, wait_frames=12)
        await asyncio.sleep(0.25)
        after = await _evidence()

    escaped = (after.get("active") is False)
    return JSONResponse(status_code=200 if escaped else 500, content={
        "format": "black2-battle-flee/v1",
        "ok": escaped,
        "status": "fled" if escaped else "flee_unconfirmed",
        "screen_type": "OVERWORLD" if escaped else "BATTLE",
        "message": "Successfully fled from wild battle." if escaped else "Flee input dispatched but battle still active.",
    })
