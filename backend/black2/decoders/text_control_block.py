"""Candidate-control trace for an observed Black 2 dialogue allocation.

This module is deliberately narrow.  The field correlations below were
observed in two bridge-owned A-edge captures of the same IREJ rev. 1 build:

* EXP_013 page 1 -> CLEAR -> page 2;
* EXP_013 page 2 -> SCROLL -> retained-old-line/new-line overlap.

It does not use a screenshot or OCR.  It traces only explicit Gen-5 line feed,
CLEAR, and SCROLL candidates.  Crucially, the sampled ``tcbl.c`` allocation
has not yet been bound to a live Window/Bitmap draw target, so this module
*never* publishes screen-visible text: its reconstructed strings are debug
candidates for EXP_013 only.  Automatic wrapping and unknown control commands
remain unresolved rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Iterable, Optional


MAIN_RAM_BASE = 0x02000000
MSG_STREAM_DELTA = 0x0C

# This is the allocation payload area tagged ``tcbl.c`` in EXP_013.  The
# offsets are measured from the batch range start, not asserted as a global
# SWAN layout.
TCBL_RANGE_OFFSET = 0x332C20
TCBL_PHASE = 0x18
TCBL_FIRST_PAGE_LATCH = 0x1C
TCBL_BMPWIN_CANDIDATE = 0x20
TCBL_BITMAP_CANDIDATE = 0x24
TCBL_CONTEXT_CANDIDATE = 0x28
TCBL_CURRENT_CHAR = 0x2C
TCBL_SCROLL_U8 = 0x37
TCBL_CURSOR_X = 0x4C
TCBL_CURSOR_Y = 0x4E
TCBL_LINE_ADVANCE = 0x52

CMD_SCROLL = 0xBE00
CMD_CLEAR = 0xBE01


@dataclass(frozen=True)
class TextToken:
    kind: str
    start: int
    end: int
    text: str = ""
    command: Optional[int] = None


@dataclass(frozen=True)
class VisibleGlyph:
    char: str
    source_address: int
    y: int


@dataclass
class TextControlBlockRender:
    """A guarded EXP_013 control-flow candidate from one atomic RAM batch.

    ``resolved`` means only that the narrow observation model parsed.  It does
    not mean a TextPrinter, Window, bitmap, pixel surface, or visible glyph has
    been verified for the current frame.
    """

    resolved: bool = False
    reason: str = "unresolved"
    phase: Optional[int] = None
    first_page_latch: Optional[int] = None
    current_char: Optional[int] = None
    cursor_x: Optional[int] = None
    cursor_y: Optional[int] = None
    line_advance: Optional[int] = None
    scroll_distance: Optional[int] = None
    pending_command: Optional[str] = None
    candidate_is_rendering: Optional[bool] = None
    candidate_awaiting_input: Optional[bool] = None
    # Must remain empty until the candidate allocation has a proven draw path
    # to the active window/pixel target.
    visible_lines: list[str] = field(default_factory=list)
    candidate_lines: list[str] = field(default_factory=list)
    glyphs: list[VisibleGlyph] = field(default_factory=list)
    stream_text: str = ""
    source_range: Optional[dict[str, str]] = None
    tcb_candidates: dict[str, str] = field(default_factory=dict)
    candidate_surface_sha256: Optional[str] = None


def _bytes(item: dict[str, Any]) -> bytes:
    values = item.get("bytes")
    if values is not None:
        return bytes(int(value) & 0xFF for value in values)
    return bytes.fromhex(str(item.get("hex", "")))


def _arm9_address(item: dict[str, Any]) -> int:
    raw = int(item.get("offset", 0))
    return raw if raw >= MAIN_RAM_BASE else MAIN_RAM_BASE + raw


def _u16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset:offset + 2], "little") if offset + 2 <= len(raw) else 0


def _u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset:offset + 4], "little") if offset + 4 <= len(raw) else 0


def _is_printable(word: int) -> bool:
    return (
        0x20 <= word <= 0x7E
        or 0x2000 <= word <= 0x206F
        or 0x3000 <= word <= 0x30FF
        or 0x4E00 <= word <= 0x9FFF
        or 0xFF01 <= word <= 0xFF5E
    )


def _parse_stream(raw: bytes, start: int, base_address: int) -> tuple[list[TextToken], Optional[int]]:
    tokens: list[TextToken] = []
    at = start
    while at + 2 <= len(raw):
        word = _u16(raw, at)
        address = base_address + at
        if word == 0xFFFF:
            return tokens, address + 2
        if word == 0xFFFE:
            tokens.append(TextToken("lf", address, address + 2))
            at += 2
            continue
        if word == 0xF000:
            if at + 6 > len(raw):
                return [], None
            command = _u16(raw, at + 2)
            argc = _u16(raw, at + 4)
            end = at + 6 + argc * 2
            if end > len(raw):
                return [], None
            tokens.append(TextToken("command", address, base_address + end, command=command))
            at = end
            continue
        if _is_printable(word):
            tokens.append(TextToken("glyph", address, address + 2, chr(word)))
            at += 2
            continue
        # A variable/unknown word cannot be safely turned into a character.
        # It is retained as a non-rendering token, rather than guessed.
        tokens.append(TextToken("unknown", address, address + 2))
        at += 2
    return [], None


def _entry_by_id(batch: dict[str, Any], name: str) -> dict[str, Any]:
    return batch.get(name, {}) if isinstance(batch.get(name, {}), dict) else {}


def _format_candidate_lines(glyphs: Iterable[VisibleGlyph], line_advance: int) -> list[str]:
    grouped: dict[int, list[str]] = {}
    for glyph in glyphs:
        # This is a narrow EXP_013 diagnostic grouping, not a Window viewport.
        # Partial scroll frames retain per-glyph Y but do not claim a row.
        if 0 <= glyph.y < line_advance * 2:
            grouped.setdefault(glyph.y, []).append(glyph.char)
    return ["".join(grouped[y]) for y in sorted(grouped)]


def decode_text_control_block(batch: dict[str, Any]) -> TextControlBlockRender:
    """Decode a guarded explicit-control *candidate* from one RAM batch.

    ``current_char`` is a continuation-cursor candidate.  The implementation therefore
    handles the two observed commands that advance that pointer before their
    pixel operation runs: a wait-state CLEAR and a wait-state SCROLL.  This is
    the crucial distinction between loaded future text and visible text.
    """

    tcb_item = _entry_by_id(batch, "dialogue_tcb")
    message_item = _entry_by_id(batch, "msg_printer_buffer")
    surface_item = _entry_by_id(batch, "dialogue_bitmap_surface")
    tcb_raw = _bytes(tcb_item)
    msg_raw = _bytes(message_item)
    if len(tcb_raw) < TCBL_LINE_ADVANCE + 2 or len(msg_raw) < MSG_STREAM_DELTA + 2:
        return TextControlBlockRender(reason="missing_tcb_or_message_range")

    phase = _u32(tcb_raw, TCBL_PHASE)
    first_page = _u32(tcb_raw, TCBL_FIRST_PAGE_LATCH)
    current_char = _u32(tcb_raw, TCBL_CURRENT_CHAR)
    cursor_x = _u16(tcb_raw, TCBL_CURSOR_X)
    cursor_y = _u16(tcb_raw, TCBL_CURSOR_Y)
    line_advance = _u16(tcb_raw, TCBL_LINE_ADVANCE)
    scroll_distance = tcb_raw[TCBL_SCROLL_U8]
    msg_base = _arm9_address(message_item)
    stream_start = msg_base + MSG_STREAM_DELTA
    tokens, stream_end = _parse_stream(msg_raw, MSG_STREAM_DELTA, msg_base)

    # The observed terminal frame parks at the end of its final glyph rather
    # than the EOS token.  Permit glyph ends, but never a command interior.
    token_boundaries = {token.start for token in tokens} | {
        token.end for token in tokens if token.kind == "glyph"
    }
    if (
        not tokens
        or stream_end is None
        or phase not in (0, 1, 2)
        or first_page not in (0, 1)
        or not (stream_start <= current_char <= stream_end)
        or current_char % 2 != 0
        or current_char not in (token_boundaries | {stream_end})
        or not (1 <= line_advance <= 64)
        or cursor_x > 320
        or cursor_y > 192
        or scroll_distance > line_advance
    ):
        return TextControlBlockRender(
            reason="tcb_structural_guard_failed",
            phase=phase,
            first_page_latch=first_page,
            current_char=current_char,
        )

    # These phase associations are observations from one dialogue fixture, not
    # a Gen-5 TextPrinter enum.  They remain explicit candidate fields.
    candidate_awaiting_input = phase in (1, 2)
    glyphs: list[VisibleGlyph] = []
    pen_y = 0
    pending_command: Optional[str] = None
    skip_next_lf = False

    for index, token in enumerate(tokens):
        if token.end > current_char:
            break
        if skip_next_lf and token.kind == "lf":
            skip_next_lf = False
            continue
        if token.kind == "glyph":
            glyphs.append(VisibleGlyph(token.text, token.start, pen_y))
            continue
        if token.kind == "lf":
            pen_y += line_advance
            continue
        if token.kind != "command":
            return TextControlBlockRender(
                reason="unknown_consumed_text_word",
                phase=phase,
                first_page_latch=first_page,
                current_char=current_char,
            )
        if token.command == CMD_CLEAR:
            # At the first page wait the consumer has stepped past CLEAR/LF
            # while the old glyphs remain in the bitmap.  Delay both actions.
            next_token = tokens[index + 1] if index + 1 < len(tokens) else None
            if next_token is None or next_token.kind != "lf":
                return TextControlBlockRender(
                    reason="candidate_command_not_followed_by_explicit_lf",
                    phase=phase,
                    first_page_latch=first_page,
                    current_char=current_char,
                )
            if phase == 1 and first_page == 1:
                pending_command = "clear"
                skip_next_lf = True
                continue
            glyphs.clear()
            pen_y = 0
            skip_next_lf = True
            continue
        if token.command == CMD_SCROLL:
            # The second page wait has the same look-ahead behaviour: its
            # source pointer is already at the next string, but its bitmap has
            # not moved until A is accepted.
            next_token = tokens[index + 1] if index + 1 < len(tokens) else None
            if next_token is None or next_token.kind != "lf":
                return TextControlBlockRender(
                    reason="candidate_command_not_followed_by_explicit_lf",
                    phase=phase,
                    first_page_latch=first_page,
                    current_char=current_char,
                )
            if phase == 1:
                pending_command = "scroll"
                skip_next_lf = True
                continue
            pending_command = "scrolling" if scroll_distance < line_advance else None
            glyphs = [
                VisibleGlyph(glyph.char, glyph.source_address, glyph.y - scroll_distance)
                for glyph in glyphs
            ]
            pen_y = line_advance
            skip_next_lf = True
            continue
        # A recognised prefix but unknown command has already been consumed.
        return TextControlBlockRender(
            reason=f"unsupported_consumed_command_0x{token.command:04X}",
            phase=phase,
            first_page_latch=first_page,
            current_char=current_char,
        )

    stream_text = "".join(token.text for token in tokens if token.kind == "glyph")
    candidate_surface = _bytes(surface_item)
    surface_hash = hashlib.sha256(candidate_surface).hexdigest() if candidate_surface else None
    return TextControlBlockRender(
        resolved=True,
        reason="candidate_explicit_control_model_exp013",
        phase=phase,
        first_page_latch=first_page,
        current_char=current_char,
        cursor_x=cursor_x,
        cursor_y=cursor_y,
        line_advance=line_advance,
        scroll_distance=scroll_distance,
        pending_command=pending_command,
        candidate_is_rendering=(phase == 0),
        candidate_awaiting_input=candidate_awaiting_input,
        candidate_lines=_format_candidate_lines(glyphs, line_advance),
        glyphs=glyphs,
        stream_text=stream_text,
        source_range={"start": f"0x{stream_start:08X}", "end": f"0x{stream_end:08X}"},
        tcb_candidates={
            "bmpwin": f"0x{_u32(tcb_raw, TCBL_BMPWIN_CANDIDATE):08X}",
            "bitmap": f"0x{_u32(tcb_raw, TCBL_BITMAP_CANDIDATE):08X}",
            "context": f"0x{_u32(tcb_raw, TCBL_CONTEXT_CANDIDATE):08X}",
        },
        candidate_surface_sha256=surface_hash,
    )
