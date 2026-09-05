"""Runtime-bound dialogue decoder layered on the existing dialogue models.

This keeps legacy text heuristics and timeline compatibility in ``dialogue.py``
but replaces the active-renderer path with the dynamically resolved
ScriptWork -> talkmsgwin -> TCBL chain.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .dialogue import DialogueDecoder, DialogueState, TextPrinterProgress, dialogue_timeline
from .dialogue_object_resolver import DynamicDialogueResolver, DialogueRuntimeLocator
from .visible_text_ledger import VisibleTextLedger

ARM9_BASE = 0x02000000


def _bytes(item: Any) -> bytes:
    if not isinstance(item, dict):
        return b""
    if item.get("bytes") is not None:
        return bytes(int(v) & 0xFF for v in item.get("bytes", []))
    try:
        return bytes.fromhex(str(item.get("hex", "")))
    except ValueError:
        return b""


def _base(item: Any) -> Optional[int]:
    if not isinstance(item, dict):
        return None
    if isinstance(item.get("addr"), int) and item["addr"] >= ARM9_BASE:
        return int(item["addr"])
    off = item.get("offset")
    if isinstance(off, int):
        return ARM9_BASE + off
    if isinstance(off, str):
        try:
            return ARM9_BASE + int(off, 0)
        except ValueError:
            return None
    return None


class RuntimeDialogueDecoder(DialogueDecoder):
    """Decode only screen-visible text after the live renderer is bound."""

    def decode(
        self,
        ram_batch: Dict[str, Any],
        frame: int = 0,
        location: str = "",
        map_section_id: Optional[int] = None,
        is_player_moving: bool = False,
        has_active_ptr: bool = False,
    ) -> DialogueState:
        if not has_active_ptr:
            return super().decode(
                ram_batch, frame=frame, location=location, map_section_id=map_section_id,
                is_player_moving=is_player_moving, has_active_ptr=False,
            )

        resolved = DynamicDialogueResolver.resolve_from_batch(ram_batch)
        if not resolved.valid:
            # Hardware/script activity is real, but it is not a visible-text
            # lifetime by itself. Close any prior renderer-backed timeline and
            # publish the source only as loaded evidence.
            dialogue_timeline.record_transition(
                text=None, frame=frame, location=location,
                map_section_id=map_section_id, is_player_moving=is_player_moving,
            )
            msg_item = ram_batch.get("msg_printer_buffer") or {}
            raw_loaded = _bytes(msg_item)
            loaded_text = ""
            msg_base = _base(msg_item)
            if raw_loaded and msg_base is not None:
                parts=[]
                for token in VisibleTextLedger(buffer_base_addr=msg_base).tokenize(raw_loaded):
                    if token.kind == "CHAR": parts.append(token.value)
                    elif token.kind == "LF": parts.append("\n")
                    elif token.kind in {"CLEAR", "SCROLL"}: parts.append(f"\n[{token.kind}]\n")
                    elif token.kind == "CMD": parts.append(token.value)
                    elif token.kind == "EOS": break
                loaded_text="".join(parts).strip()
            return DialogueState(
                active=True, awaiting_input=False, current_text="", loaded_text=loaded_text,
                full_dialogue_text="", speaker="说话者未解析", speaker_category="UNRESOLVED",
                recent_history=dialogue_timeline.get_history(limit=20),
                printer=TextPrinterProgress(
                    is_active=True, current_page_text="", lines=[], loaded_text=loaded_text,
                    visible_text_confidence="unresolved",
                    visible_text_source="hardware message activity present; live TextPrinter/Window binding unresolved",
                    renderer_kind="unresolved",
                    candidate_model_reason=resolved.disambiguation_reason,
                ),
            )

        msg_item = ram_batch.get("dialogue_live_strbuf") or ram_batch.get("msg_printer_buffer") or {}
        raw_msg = _bytes(msg_item)
        msg_base = _base(msg_item)
        if not raw_msg or msg_base is None:
            return super().decode(
                ram_batch, frame=frame, location=location, map_section_id=map_section_id,
                is_player_moving=is_player_moving, has_active_ptr=True,
            )

        pixel_item = ram_batch.get("pixeldata_surface") or {}
        raw_px = _bytes(pixel_item) or None
        ledger = VisibleTextLedger(buffer_base_addr=msg_base)
        snap = ledger.resolve_visible_text(
            raw_msg_bytes=raw_msg,
            source_cursor=resolved.source_cursor or 0,
            phase=resolved.phase if resolved.phase is not None else -1,
            first_page_latch=resolved.first_page_latch if resolved.first_page_latch is not None else 0,
            pixel_bytes=raw_px,
        )
        visible_text = snap.text
        loaded_parts = []
        for token in ledger.tokenize(raw_msg):
            if token.kind == "CHAR":
                loaded_parts.append(token.value)
            elif token.kind == "LF":
                loaded_parts.append("\n")
            elif token.kind in {"CLEAR", "SCROLL"}:
                loaded_parts.append(f"\n[{token.kind}]\n")
            elif token.kind == "CMD":
                loaded_parts.append(token.value)
            elif token.kind == "EOS":
                break
        loaded_source = "".join(loaded_parts).strip()

        _, entry = dialogue_timeline.record_render_lifecycle(
            active=True, text=visible_text, frame=frame, location=location,
            map_section_id=map_section_id, is_player_moving=is_player_moving,
        )
        actor = DialogueRuntimeLocator.parent_actor_from_batch(ram_batch, resolved)
        if actor:
            speaker = f"Runtime FieldActor (Model {actor.get('model_id')})"
            speaker_category = "NPC"
            speaker_source = actor.get("source") or "ScriptWork ParentActor"
            if entry:
                entry.speaker = speaker
                entry.speaker_category = speaker_category
        else:
            speaker = "说话者未解析"
            speaker_category = "UNRESOLVED"
            speaker_source = "ScriptWork ParentActor not sampled/coherent"

        state = DialogueState(
            active=True,
            awaiting_input=bool(snap.phase_code in (1, 2)),
            current_text=visible_text,
            loaded_text=loaded_source,
            full_dialogue_text=visible_text,
            speaker=speaker,
            speaker_category=speaker_category,
            active_pointer=f"0x{resolved.tcbl_addr:08X}" if resolved.tcbl_addr else None,
            start_time=entry.start_time if entry else None,
            duration_seconds=entry.duration_seconds if entry else 0.0,
            recent_history=dialogue_timeline.get_history(limit=20),
            printer=TextPrinterProgress(
                is_active=True,
                is_printing=(snap.phase_code == 0),
                waiting_for_input=bool(snap.phase_code in (1, 2)),
                current_line=len(snap.lines),
                full_text=loaded_source,
                current_page_text=visible_text,
                lines=snap.lines,
                visible_text_confidence="verified_for_tested_dialogue" if snap.pixel_verified else "probable",
                visible_text_source="ScriptWork -> talkmsgwin -> TCBL + VisibleTextLedger",
                loaded_text=loaded_source,
                renderer_kind="runtime_bound_token_stream",
                control_phase=snap.phase_code,
                first_page_latch=1 if snap.is_first_page else 0,
                current_char_pointer=f"0x{snap.cursor_addr:08X}" if snap.cursor_addr else None,
                source_range={
                    "strbuf": f"0x{resolved.strbuf_addr:08X}" if resolved.strbuf_addr else "unresolved",
                    "talkmsgwin": f"0x{resolved.talkmsgwin_addr:08X}" if resolved.talkmsgwin_addr else "unresolved",
                    "tcbl": f"0x{resolved.tcbl_addr:08X}" if resolved.tcbl_addr else "unresolved",
                    "speaker": speaker_source,
                },
                candidate_model_confidence="probable",
                candidate_model_reason=resolved.disambiguation_reason,
                candidate_lines=snap.lines,
                candidate_control_phase=snap.phase_code,
                candidate_first_page_latch=1 if snap.is_first_page else 0,
                candidate_continuation_cursor=f"0x{snap.cursor_addr:08X}" if snap.cursor_addr else None,
            ),
        )
        return state
