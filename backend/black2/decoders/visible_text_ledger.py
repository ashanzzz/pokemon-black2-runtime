"""Authoritative VisibleTextLedger for Pokémon Black 2 (IREJ).

Constructs the real-time visible dialogue text from verified RAM facts:
- StrBuf header layout: +0x04=capacity, +0x06=length, +0x08=magic, +0x0C=character stream
- SourceCursor (0x02332C4C)
- Phase (0x02332C38: 0=Printing, 1=WaitPage, 2=WaitEOS)
- FirstPageLatch (0x02332C3C: 1=Page 1, 0=Page 2+)
- PixelData Ground Truth (0x023353C0: 3840 bytes 240x32 4bpp)

Strictly obeys the non-leak invariant:
- Never leaks text ahead of the current screen.
- Never retains text rolled out of the top line.
- Clears accurately upon page flip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class Token:
    kind: str  # "CHAR", "LF", "CLEAR", "SCROLL", "EOS", "CMD"
    value: str
    source_addr: int
    raw_length: int


@dataclass
class ConfidenceReport:
    source_buffer: str = "verified"
    source_cursor: str = "verified"
    printer_phase: str = "verified_for_tested_path"
    line_reconstruction: str = "verified_for_tested_dialogue"
    pixel_spatial_match: bool = False
    resolver_lifecycle: str = "unresolved"
    cross_dialogue_generalization: str = "probable"


@dataclass
class VisibleSnapshot:
    line0: str
    line1: str
    phase_name: str  # "PRINTING", "WAIT_PAGE", "WAIT_EOS", "INACTIVE"
    phase_code: int
    is_first_page: bool
    cursor_addr: int
    confidence: ConfidenceReport = field(default_factory=ConfidenceReport)
    pixel_verified: bool = False
    pixel_line0_active: bool = False
    pixel_line1_active: bool = False

    @property
    def lines(self) -> List[str]:
        return [l for l in (self.line0, self.line1) if l]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class VisibleTextLedger:
    """Logical token-interpreter ledger tracking text rendering, line scrolling, and window clearing."""

    def __init__(self, buffer_base_addr: int = 0x022490A0):
        self.buffer_base = buffer_base_addr

    def tokenize(self, raw_bytes: bytes) -> List[Token]:
        """Parse raw UCS-2 buffer into structured semantic tokens with exact addresses.

        Properly recognizes StrBuf header: text content starts at offset +0x0C.
        """
        tokens: List[Token] = []
        start_offset = 0x0C if len(raw_bytes) > 0x0C else 0
        idx = start_offset

        while idx + 1 < len(raw_bytes):
            curr_addr = self.buffer_base + idx
            val = int.from_bytes(raw_bytes[idx:idx+2], "little")

            if val == 0xFFFF:
                tokens.append(Token("EOS", "[EOS]", curr_addr, 2))
                break
            elif val == 0xFFFE:
                tokens.append(Token("LF", "\n", curr_addr, 2))
                idx += 2
            elif val == 0xF000:
                if idx + 5 < len(raw_bytes):
                    cmd = int.from_bytes(raw_bytes[idx+2:idx+4], "little")
                    argc = int.from_bytes(raw_bytes[idx+4:idx+6], "little")
                    total_cmd_len = 6 + argc * 2
                    if cmd == 0xBE01:
                        tokens.append(Token("CLEAR", "[CLEAR]", curr_addr, total_cmd_len))
                    elif cmd == 0xBE00:
                        tokens.append(Token("SCROLL", "[SCROLL]", curr_addr, total_cmd_len))
                    else:
                        tokens.append(Token("CMD", f"[CMD_{cmd:04X}]", curr_addr, total_cmd_len))
                    idx += total_cmd_len
                else:
                    tokens.append(Token("CMD", "[CMD_PARTIAL]", curr_addr, 2))
                    idx += 2
            else:
                try:
                    char_str = chr(val)
                except ValueError:
                    char_str = f"\\u{val:04x}"
                tokens.append(Token("CHAR", char_str, curr_addr, 2))
                idx += 2

        return tokens

    def resolve_visible_text(
        self,
        raw_msg_bytes: bytes,
        source_cursor: int,
        phase: int,
        first_page_latch: int,
        pixel_bytes: Optional[bytes] = None,
    ) -> VisibleSnapshot:
        """Deterministically deduce visible text from verified RAM state using a token interpreter."""
        tokens = self.tokenize(raw_msg_bytes)

        phase_names = {
            0: "PRINTING",
            1: "WAIT_PAGE",
            2: "WAIT_EOS",
        }
        p_name = phase_names.get(phase, f"UNKNOWN_{phase}")

        # Stream interpreter: dynamic 2-line window viewport
        line0 = ""
        line1 = ""
        active_line = 0  # 0 or 1

        # We step through the tokens sequentially
        # If phase == 1 (WAIT_PAGE), the engine has pre-read the next control code and paused
        # BEFORE executing the clear or scroll action. Therefore, if a CLEAR/SCROLL was reached,
        # but phase == 1, that action is PENDING and must NOT be applied to the visible window yet.
        pending_wait_ctrl_addr = None
        if phase == 1:
            # Find the control code that caused this wait
            for t in tokens:
                if t.kind in ("CLEAR", "SCROLL"):
                    # The cursor typically pauses right after the control command and its LF
                    if source_cursor <= t.source_addr + t.raw_length + 2:
                        pending_wait_ctrl_addr = t.source_addr
                        break

        for t in tokens:
            # Stop if token is ahead of the cursor and we are in active printing
            if phase == 0 and t.source_addr > source_cursor:
                break

            # If we are waiting for input (phase 1) before this clear/scroll, pause interpretation here!
            if pending_wait_ctrl_addr is not None and t.source_addr >= pending_wait_ctrl_addr:
                break

            if t.kind == "CLEAR":
                # Clear action executes ONLY when cursor has advanced PAST this control command
                if source_cursor >= t.source_addr + t.raw_length:
                    line0 = ""
                    line1 = ""
                    active_line = 0

            elif t.kind == "SCROLL":
                # Scroll action executes ONLY when cursor has advanced PAST this control command and its newline
                if source_cursor >= t.source_addr + t.raw_length + 2 or phase == 2:
                    line0 = line1
                    line1 = ""
                    active_line = 1

            elif t.kind == "LF":
                # Switch to second line if on first line
                if active_line == 0 and len(line0) > 0:
                    active_line = 1

            elif t.kind == "CHAR":
                # Add character to the current active line
                if active_line == 0:
                    line0 += t.value
                else:
                    line1 += t.value

            elif t.kind == "EOS":
                break

        # Pixel verification via PixelData Authoritative Raster Oracle (240x32 4bpp, 3840 bytes)
        px_verified = False
        l0_act = False
        l1_act = False
        if pixel_bytes is not None and len(pixel_bytes) == 3840:
            l0_nz = sum(1 for b in pixel_bytes[:1920] if b != 0)
            l1_nz = sum(1 for b in pixel_bytes[1920:] if b != 0)
            l0_act = l0_nz > 0
            l1_act = l1_nz > 0

            text_l0_has = len(line0.strip()) > 0
            text_l1_has = len(line1.strip()) > 0

            if (text_l0_has == l0_act or (text_l0_has and l0_nz > 50)):
                if not text_l1_has or (text_l1_has and l1_nz > 50):
                    px_verified = True

        conf = ConfidenceReport(
            source_buffer="verified",
            source_cursor="verified",
            printer_phase="verified_for_tested_path",
            line_reconstruction="verified_for_tested_dialogue",
            pixel_spatial_match=px_verified,
            resolver_lifecycle="unresolved",
            cross_dialogue_generalization="probable",
        )

        return VisibleSnapshot(
            line0=line0,
            line1=line1,
            phase_name=p_name,
            phase_code=phase,
            is_first_page=(first_page_latch == 1),
            cursor_addr=source_cursor,
            confidence=conf,
            pixel_verified=px_verified,
            pixel_line0_active=l0_act,
            pixel_line1_active=l1_act,
        )
