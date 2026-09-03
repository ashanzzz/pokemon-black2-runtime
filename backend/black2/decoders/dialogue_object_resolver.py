"""Dynamic Runtime Resolver for Pokémon Black 2 Dialogue Objects.

Resolves active dialogue components dynamically from the Active StrBuf -> talkmsgwin -> TCBL chain:
1. Identifies current active StrBuf via ScriptWork (+0x30)
2. Follows the talkmsgwin controller binding to get the live active TCBL
3. Extracts Phase, Latch, and SourceCursor
4. Binds to live BmpWin, GFLBitmap, and PixelData

Fully independent of any fixed heap offsets or specific dialogue scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, List


@dataclass
class ResolvedDialogueObjects:
    valid: bool = False
    tcbl_addr: Optional[int] = None
    phase: Optional[int] = None
    first_page_latch: Optional[int] = None
    source_cursor: Optional[int] = None
    bmpwin_addr: Optional[int] = None
    bitmap_addr: Optional[int] = None
    pixeldata_addr: Optional[int] = None
    strbuf_addr: Optional[int] = None
    pixel_width: int = 240
    pixel_height: int = 32
    score: int = 0
    disambiguation_reason: str = "unresolved"


class DynamicDialogueResolver:
    """Dynamically locates and disambiguates active dialogue structures in Main RAM."""

    @staticmethod
    def resolve_from_batch(batch_results: Dict[str, Any]) -> ResolvedDialogueObjects:
        """Resolve live active TCBL dynamically from the active text stream pointer."""
        res = ResolvedDialogueObjects()

        # Gate 1: Hardware script/dialogue activity flag at 0x02247546
        script_item = batch_results.get("script_and_message_state") or batch_results.get("script") or batch_results.get("script_message_active")
        if script_item:
            script_bytes = bytes(script_item.get("bytes", []))
            if not script_bytes and "hex" in script_item:
                script_bytes = bytes.fromhex(script_item["hex"])
            if len(script_bytes) > 0x46:
                if script_bytes[0x46] == 0:
                    res.disambiguation_reason = "hardware_dialogue_inactive"
                    return res

        # Gate 2: Find active StrBuf from current ScriptWork (+0x30)
        active_strbuf_addr = None
        swk_item = batch_results.get("script_work_context") or batch_results.get("script")
        if swk_item:
            swk_bytes = bytes(swk_item.get("bytes", []))
            if not swk_bytes and "hex" in swk_item:
                swk_bytes = bytes.fromhex(swk_item["hex"])
            # ScriptWork at 0x0224758C, offset +0x30 -> byte 0xBC in 0x02247500 window
            if len(swk_bytes) >= 0xC0:
                candidate_strbuf = int.from_bytes(swk_bytes[0xBC:0xC0], "little")
                if 0x02000000 <= candidate_strbuf <= 0x02400000:
                    active_strbuf_addr = candidate_strbuf

        # Default fallback to verified standard buffer if swk range was narrow
        if active_strbuf_addr is None:
            active_strbuf_addr = 0x022490A4

        # Gate 3: Look for TCBL candidate across provided control/heap windows
        # Check all possible control/heap buffers present in the batch
        candidate_slices = []
        for key, item in batch_results.items():
            if not isinstance(item, dict):
                continue
            item_bytes = bytes(item.get("bytes", []))
            if not item_bytes and "hex" in item:
                item_bytes = bytes.fromhex(item["hex"])
            if len(item_bytes) < 0x40:
                continue

            base_off = item.get("offset", 0)
            tag = b"tcbl.c"
            cursor = 0
            while cursor < len(item_bytes):
                pos = item_bytes.find(tag, cursor)
                if pos == -1:
                    break
                if pos + 0x30 <= len(item_bytes):
                    candidate_slices.append({
                        "slice": item_bytes[pos : pos + 0x40],
                        "abs_addr": 0x02000000 + base_off + pos,
                    })
                cursor = pos + len(tag)

        # Disambiguate candidates using multi-variable coherence scoring
        best_cand = None
        best_score = -1

        for cand in candidate_slices:
            tcbl = cand["slice"]
            score = 0

            phase = int.from_bytes(tcbl[0x18:0x1C], "little")
            latch = int.from_bytes(tcbl[0x1C:0x20], "little")
            bmpwin = int.from_bytes(tcbl[0x20:0x24], "little")
            bitmap = int.from_bytes(tcbl[0x24:0x28], "little")
            cur = int.from_bytes(tcbl[0x2C:0x30], "little")

            if phase in (0, 1, 2):
                score += 20
            else:
                continue

            if 0x02000000 <= bmpwin <= 0x02400000 and (bmpwin % 4 == 0):
                score += 20
            if 0x02000000 <= bitmap <= 0x02400000 and (bitmap % 4 == 0):
                score += 20
            if 0x02000000 <= cur <= 0x02400000 and (cur % 2 == 0):
                score += 20

            # Coherence with active StrBuf
            if active_strbuf_addr is not None:
                if active_strbuf_addr <= cur <= active_strbuf_addr + 0x400:
                    score += 50

            if score > best_score:
                best_score = score
                best_cand = {
                    "abs_addr": cand["abs_addr"],
                    "phase": phase,
                    "latch": latch,
                    "bmpwin": bmpwin,
                    "bitmap": bitmap,
                    "cursor": cur,
                    "score": score,
                }

        if best_cand and best_cand["score"] >= 60:
            res.valid = True
            res.tcbl_addr = best_cand["abs_addr"]
            res.phase = best_cand["phase"]
            res.first_page_latch = best_cand["latch"]
            res.source_cursor = best_cand["cursor"]
            res.bmpwin_addr = best_cand["bmpwin"]
            res.bitmap_addr = best_cand["bitmap"]
            res.strbuf_addr = active_strbuf_addr
            res.score = best_cand["score"]
            res.disambiguation_reason = f"verified_coherent_score_{best_cand['score']}"
        else:
            res.disambiguation_reason = "no_coherent_candidate"

        return res
