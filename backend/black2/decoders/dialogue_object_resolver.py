"""Runtime-bound dialogue object resolver for Pokémon Black 2 IREJ v1.1.

The production path is cache-first and bounded.  It resolves the active
ScriptWork main StrBuf, then finds the matching ``talkmsgwin.c`` allocation,
and validates the linked TCBL/BmpWin/Bitmap/PixelData objects.  A full 4 MiB
scan is never scheduled by normal dialogue polling.

The pure ``resolve_from_ram`` helper exists for saved Universal Evidence only.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.reader import MemoryReader

ARM9_BASE = 0x02000000
ARM9_END = 0x02400000
SCRIPT_WORK_TAG = b"script_work.c"
TALK_TAG = b"talkmsgwin.c"
TCBL_TAG = b"tcbl.c"
SCRIPT_WORK_PAYLOAD_DELTA = 0x18
SCRIPT_WORK_PARENT_ACTOR = 0x08
SCRIPT_WORK_MAIN_STRBUF = 0x30
TALK_PHASE_PTR = 0x9C
TALK_BMPWIN = 0xA0
TALK_STRBUF = 0xA8
TCBL_PHASE = 0x18
TCBL_LATCH = 0x1C
TCBL_BMPWIN = 0x20
TCBL_BITMAP = 0x24
TCBL_CURSOR = 0x2C
BITMAP_PIXELS = 0x00
BITMAP_SIZE_PACKED = 0x04
# Current field-message allocations have repeatedly lived in this bounded heap
# neighborhood.  The range is searched only on cache miss / lifecycle change.
SCRIPT_SCAN_START = 0x00240000
SCRIPT_SCAN_SIZE = 0x00020000
TALK_SCAN_START = 0x00320000
TALK_SCAN_SIZE = 0x00018000


def _valid_ptr(value: Optional[int], *, aligned: int = 1) -> bool:
    return isinstance(value, int) and ARM9_BASE <= value < ARM9_END and value % aligned == 0


def _item_bytes(item: Any) -> bytes:
    if not isinstance(item, dict):
        return b""
    values = item.get("bytes")
    if values is not None:
        return bytes(int(v) & 0xFF for v in values)
    try:
        return bytes.fromhex(str(item.get("hex", "")))
    except ValueError:
        return b""


def _item_abs_base(item: Any) -> Optional[int]:
    if not isinstance(item, dict):
        return None
    raw = item.get("addr")
    if isinstance(raw, int) and raw >= ARM9_BASE:
        return raw
    off = item.get("offset")
    if isinstance(off, int):
        return ARM9_BASE + off
    if isinstance(off, str):
        try:
            return ARM9_BASE + int(off, 0)
        except ValueError:
            return None
    return None


def _u16(data: bytes, off: int) -> Optional[int]:
    return int.from_bytes(data[off:off + 2], "little") if off >= 0 and off + 2 <= len(data) else None


def _s16(data: bytes, off: int) -> Optional[int]:
    return int.from_bytes(data[off:off + 2], "little", signed=True) if off >= 0 and off + 2 <= len(data) else None


def _u32(data: bytes, off: int) -> Optional[int]:
    return int.from_bytes(data[off:off + 4], "little") if off >= 0 and off + 4 <= len(data) else None


def _s32(data: bytes, off: int) -> Optional[int]:
    return int.from_bytes(data[off:off + 4], "little", signed=True) if off >= 0 and off + 4 <= len(data) else None


@dataclass
class ResolvedDialogueObjects:
    valid: bool = False
    script_work_addr: Optional[int] = None
    talkmsgwin_addr: Optional[int] = None
    tcbl_addr: Optional[int] = None
    phase: Optional[int] = None
    first_page_latch: Optional[int] = None
    source_cursor: Optional[int] = None
    bmpwin_addr: Optional[int] = None
    bitmap_addr: Optional[int] = None
    pixeldata_addr: Optional[int] = None
    strbuf_addr: Optional[int] = None
    parent_actor_addr: Optional[int] = None
    pixel_width: int = 240
    pixel_height: int = 32
    score: int = 0
    disambiguation_reason: str = "unresolved"

    def public(self) -> Dict[str, Any]:
        value = asdict(self)
        for key in (
            "script_work_addr", "talkmsgwin_addr", "tcbl_addr", "source_cursor",
            "bmpwin_addr", "bitmap_addr", "pixeldata_addr", "strbuf_addr", "parent_actor_addr",
        ):
            if isinstance(value.get(key), int):
                value[key] = f"0x{value[key]:08X}"
        return value


def _script_work_from_context(item: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    data = _item_bytes(item)
    base = _item_abs_base(item)
    if not data or base is None:
        return None, None, None
    candidates: list[tuple[int, int, Optional[int]]] = []
    cursor = 0
    while True:
        pos = data.find(SCRIPT_WORK_TAG, cursor)
        if pos < 0:
            break
        work_off = pos + SCRIPT_WORK_PAYLOAD_DELTA
        strbuf = _u32(data, work_off + SCRIPT_WORK_MAIN_STRBUF)
        parent = _u32(data, work_off + SCRIPT_WORK_PARENT_ACTOR)
        if _valid_ptr(strbuf, aligned=4):
            score = 10 + (4 if _valid_ptr(parent, aligned=4) else 0)
            candidates.append((score, base + work_off, parent))
        cursor = pos + 1
    if not candidates:
        return None, None, None
    candidates.sort(reverse=True)
    _score, work_addr, parent = candidates[0]
    rel = work_addr - base
    return work_addr, _u32(data, rel + SCRIPT_WORK_MAIN_STRBUF), parent if _valid_ptr(parent, aligned=4) else None


def _validate_talk_block(
    talk_addr: int,
    talk: bytes,
    active_strbuf: int,
    tcbl_addr: int,
    tcbl: bytes,
    bitmap: Optional[bytes] = None,
    script_work_addr: Optional[int] = None,
    parent_actor_addr: Optional[int] = None,
) -> ResolvedDialogueObjects:
    out = ResolvedDialogueObjects(
        script_work_addr=script_work_addr,
        talkmsgwin_addr=talk_addr,
        strbuf_addr=active_strbuf,
        parent_actor_addr=parent_actor_addr,
    )
    if not (talk.startswith(TALK_TAG) and tcbl.startswith(TCBL_TAG)):
        out.disambiguation_reason = "allocation_tags_do_not_match"
        return out
    talk_phase_ptr = _u32(talk, TALK_PHASE_PTR)
    talk_bmpwin = _u32(talk, TALK_BMPWIN)
    talk_strbuf = _u32(talk, TALK_STRBUF)
    phase = _u32(tcbl, TCBL_PHASE)
    latch = _u32(tcbl, TCBL_LATCH)
    bmpwin = _u32(tcbl, TCBL_BMPWIN)
    bitmap_addr = _u32(tcbl, TCBL_BITMAP)
    source_cursor = _u32(tcbl, TCBL_CURSOR)
    score = 0
    if talk_strbuf == active_strbuf:
        score += 40
    if talk_phase_ptr == tcbl_addr + TCBL_PHASE:
        score += 40
    if phase in (0, 1, 2):
        score += 20
    if latch in (0, 1):
        score += 10
    if _valid_ptr(bmpwin, aligned=4) and bmpwin == talk_bmpwin:
        score += 25
    if _valid_ptr(bitmap_addr, aligned=4):
        score += 10
    if _valid_ptr(source_cursor, aligned=2) and active_strbuf <= source_cursor <= active_strbuf + 0x800:
        score += 30
    pixeldata_addr = None
    width, height = 240, 32
    if bitmap:
        pixeldata_addr = _u32(bitmap, BITMAP_PIXELS)
        packed = _u32(bitmap, BITMAP_SIZE_PACKED)
        if isinstance(packed, int):
            w = packed & 0xFFFF
            h = (packed >> 16) & 0xFFFF
            if 1 <= w <= 1024 and 1 <= h <= 1024:
                width, height = w, h
                score += 10
        if _valid_ptr(pixeldata_addr, aligned=4):
            score += 10
    out.valid = score >= 150
    out.tcbl_addr = tcbl_addr
    out.phase = phase
    out.first_page_latch = latch
    out.source_cursor = source_cursor
    out.bmpwin_addr = bmpwin
    out.bitmap_addr = bitmap_addr
    out.pixeldata_addr = pixeldata_addr
    out.pixel_width = width
    out.pixel_height = height
    out.score = score
    out.disambiguation_reason = (
        f"coherent_ScriptWork_talkmsgwin_TCBL_score_{score}" if out.valid
        else f"dialogue_object_coherence_score_{score}_below_threshold"
    )
    return out


def _actor_summary(item: Any, expected_addr: Optional[int]) -> Dict[str, Any]:
    data = _item_bytes(item)
    base = _item_abs_base(item)
    if not data or base is None or expected_addr != base or len(data) < 0x8C:
        return {}
    backref = _u32(data, 0x88)
    face = _u16(data, 0x18)
    return {
        "address": f"0x{base:08X}",
        "actor_uid": _u16(data, 0x08),
        "zone_id_raw": _u16(data, 0x0A),
        "model_id": _u16(data, 0x0C),
        "script_id": _u16(data, 0x14),
        "face_dir_raw": face,
        "grid": {"x": _u16(data, 0x3C), "y": _s16(data, 0x3E), "z": _u16(data, 0x40)},
        "world": {
            "x": (_s32(data, 0x44) or 0) / 4096.0,
            "y": (_s32(data, 0x48) or 0) / 4096.0,
            "z": (_s32(data, 0x4C) or 0) / 4096.0,
        },
        "actor_system_backref": f"0x{backref:08X}" if _valid_ptr(backref, aligned=4) else None,
        "confidence": "probable",
        "source": "ScriptWork+0x08 ParentActor accessor + coherent FieldActor structure",
    }


class DynamicDialogueResolver:
    """Compatibility resolver for an already-sampled atomic batch."""

    @staticmethod
    def resolve_from_batch(batch_results: Dict[str, Any]) -> ResolvedDialogueObjects:
        dynamic = DialogueRuntimeLocator.resolve_cached_batch(batch_results)
        if dynamic.valid:
            return dynamic

        work_addr, active_strbuf, parent = _script_work_from_context(batch_results.get("script_work_context"))
        if not _valid_ptr(active_strbuf, aligned=4):
            return ResolvedDialogueObjects(
                script_work_addr=work_addr, parent_actor_addr=parent,
                disambiguation_reason="active_ScriptWork_StrBuf_unresolved",
            )
        best = ResolvedDialogueObjects(
            script_work_addr=work_addr, strbuf_addr=active_strbuf, parent_actor_addr=parent,
            disambiguation_reason="no_coherent_candidate",
        )
        for item in batch_results.values():
            data = _item_bytes(item)
            base = _item_abs_base(item)
            if not data or base is None:
                continue
            pos = 0
            while True:
                hit = data.find(TCBL_TAG, pos)
                if hit < 0:
                    break
                tcbl = data[hit:hit + 0x40]
                if len(tcbl) >= 0x30:
                    tcbl_addr = base + hit
                    phase = _u32(tcbl, TCBL_PHASE)
                    cursor = _u32(tcbl, TCBL_CURSOR)
                    score = 0
                    if phase in (0, 1, 2): score += 20
                    if _valid_ptr(cursor, aligned=2) and active_strbuf <= cursor <= active_strbuf + 0x800: score += 50
                    if score > best.score:
                        best = ResolvedDialogueObjects(
                            valid=score >= 60, script_work_addr=work_addr, tcbl_addr=tcbl_addr,
                            phase=phase, first_page_latch=_u32(tcbl, TCBL_LATCH),
                            source_cursor=cursor, bmpwin_addr=_u32(tcbl, TCBL_BMPWIN),
                            bitmap_addr=_u32(tcbl, TCBL_BITMAP), strbuf_addr=active_strbuf,
                            parent_actor_addr=parent, score=score,
                            disambiguation_reason=f"legacy_batch_tcbl_score_{score}",
                        )
                pos = hit + 1
        return best


class DialogueRuntimeLocator:
    """Cache active dialogue addresses and rediscover them only on lifecycle change."""

    def __init__(self) -> None:
        self.cached: Optional[ResolvedDialogueObjects] = None
        self.discovery_count = 0
        self.last_discovery_attempt = 0.0
        self.min_discovery_interval = 1.5
        self.last_discovery_reason = ""

    def invalidate(self) -> None:
        self.cached = None

    def sample_specs(self) -> list[Dict[str, Any]]:
        c = self.cached
        if not c or not c.valid:
            return []
        specs = [
            {"id": "dialogue_live_strbuf", "addr": int(c.strbuf_addr) - 4, "length": 0x404},
            {"id": "dialogue_live_talk", "addr": int(c.talkmsgwin_addr), "length": 0xB0},
            {"id": "dialogue_live_tcbl", "addr": int(c.tcbl_addr), "length": 0x40},
        ]
        if _valid_ptr(c.script_work_addr, aligned=4):
            specs.append({"id": "dialogue_live_script_work", "addr": int(c.script_work_addr) - SCRIPT_WORK_PAYLOAD_DELTA, "length": 0x60})
        if _valid_ptr(c.bitmap_addr, aligned=4):
            specs.append({"id": "dialogue_live_bitmap", "addr": int(c.bitmap_addr), "length": 0x20})
        if _valid_ptr(c.pixeldata_addr, aligned=4):
            pixel_len = max(1, min(0x8000, c.pixel_width * c.pixel_height // 2))
            specs.append({"id": "pixeldata_surface", "addr": int(c.pixeldata_addr), "length": pixel_len})
        if _valid_ptr(c.parent_actor_addr, aligned=4):
            specs.append({"id": "dialogue_parent_actor", "addr": int(c.parent_actor_addr), "length": 0x100})
        return specs

    @staticmethod
    def resolve_cached_batch(batch_results: Dict[str, Any]) -> ResolvedDialogueObjects:
        talk_item = batch_results.get("dialogue_live_talk")
        tcbl_item = batch_results.get("dialogue_live_tcbl")
        strbuf_item = batch_results.get("dialogue_live_strbuf")
        if not (talk_item and tcbl_item and strbuf_item):
            return ResolvedDialogueObjects(disambiguation_reason="no_cached_dynamic_ranges")
        talk = _item_bytes(talk_item)
        tcbl = _item_bytes(tcbl_item)
        talk_addr = _item_abs_base(talk_item)
        tcbl_addr = _item_abs_base(tcbl_item)
        strbuf_base = _item_abs_base(strbuf_item)
        if None in (talk_addr, tcbl_addr, strbuf_base):
            return ResolvedDialogueObjects(disambiguation_reason="dynamic_range_missing_address")
        active_strbuf = int(strbuf_base) + 4
        bitmap = _item_bytes(batch_results.get("dialogue_live_bitmap"))
        work_item = batch_results.get("dialogue_live_script_work") or batch_results.get("script_work_context")
        work_addr, work_strbuf, parent = _script_work_from_context(work_item)
        if _valid_ptr(work_strbuf, aligned=4) and work_strbuf != active_strbuf:
            return ResolvedDialogueObjects(disambiguation_reason="cached_StrBuf_no_longer_matches_ScriptWork")
        return _validate_talk_block(
            int(talk_addr), talk, active_strbuf, int(tcbl_addr), tcbl, bitmap or None,
            script_work_addr=work_addr, parent_actor_addr=parent,
        )

    async def discover(self, reader: "MemoryReader", batch_results: Dict[str, Any]) -> ResolvedDialogueObjects:
        now = time.monotonic()
        if self.last_discovery_attempt and now - self.last_discovery_attempt < self.min_discovery_interval:
            return ResolvedDialogueObjects(disambiguation_reason=self.last_discovery_reason or "dialogue_rediscovery_throttled")
        self.last_discovery_attempt = now
        script_candidates: list[tuple[int, int, Optional[int]]] = []
        work_addr, active_strbuf, parent = _script_work_from_context(batch_results.get("script_work_context"))
        if _valid_ptr(active_strbuf, aligned=4) and _valid_ptr(work_addr, aligned=4):
            script_candidates.append((int(work_addr), int(active_strbuf), parent))
        else:
            script_scan = await reader.scan_pattern_snapshot(
                list(SCRIPT_WORK_TAG), start=SCRIPT_SCAN_START, size=SCRIPT_SCAN_SIZE, limit=16,
            )
            raw_matches = script_scan.get("matches", []) if isinstance(script_scan, dict) else []
            script_addrs=[]
            for raw in raw_matches:
                try: off=int(raw)
                except (TypeError, ValueError): continue
                addr=off if off >= ARM9_BASE else ARM9_BASE+off
                if _valid_ptr(addr): script_addrs.append(addr)
            if script_addrs:
                sw_payload=await reader.read_batch_snapshot([
                    {"id":f"script_{i}","addr":addr,"length":0x60}
                    for i,addr in enumerate(script_addrs)
                ])
                sw_results=sw_payload.get("results", {})
                for i,_addr in enumerate(script_addrs):
                    wa,sb,pa=_script_work_from_context(sw_results.get(f"script_{i}"))
                    if _valid_ptr(wa,aligned=4) and _valid_ptr(sb,aligned=4):
                        script_candidates.append((int(wa),int(sb),pa))
        if not script_candidates:
            self.last_discovery_reason = "active_ScriptWork_StrBuf_unresolved"
            self.invalidate()
            return ResolvedDialogueObjects(disambiguation_reason=self.last_discovery_reason)

        scan = await reader.scan_pattern_snapshot(
            list(TALK_TAG), start=TALK_SCAN_START, size=TALK_SCAN_SIZE, limit=16,
        )
        matches = scan.get("matches", []) if isinstance(scan, dict) else []
        candidates: list[int] = []
        for raw in matches:
            try:
                off = int(raw)
            except (TypeError, ValueError):
                continue
            addr = off if off >= ARM9_BASE else ARM9_BASE + off
            if _valid_ptr(addr):
                candidates.append(addr)
        if not candidates:
            self.last_discovery_reason = "bounded_talkmsgwin_scan_found_no_candidates"
            self.invalidate()
            return ResolvedDialogueObjects(disambiguation_reason=self.last_discovery_reason)

        talk_payload = await reader.read_batch_snapshot([
            {"id": f"talk_{i}", "addr": addr, "length": 0xB0}
            for i, addr in enumerate(candidates)
        ])
        results = talk_payload.get("results", {})
        selected: list[tuple[int, int, bytes, int, int, int, Optional[int]]] = []
        script_by_strbuf={sb:(wa,pa) for wa,sb,pa in script_candidates}
        for i, addr in enumerate(candidates):
            talk = _item_bytes(results.get(f"talk_{i}"))
            talk_strbuf=_u32(talk,TALK_STRBUF)
            owner=script_by_strbuf.get(talk_strbuf)
            if not talk.startswith(TALK_TAG) or owner is None:
                continue
            phase_ptr = _u32(talk, TALK_PHASE_PTR)
            if not _valid_ptr(phase_ptr, aligned=4):
                continue
            tcbl_addr = int(phase_ptr) - TCBL_PHASE
            if _valid_ptr(tcbl_addr, aligned=4):
                wa,pa=owner
                selected.append((i, addr, talk, tcbl_addr, int(talk_strbuf), wa, pa))
        if not selected:
            self.last_discovery_reason = "no_talkmsgwin_matches_active_StrBuf"
            self.invalidate()
            return ResolvedDialogueObjects(disambiguation_reason=self.last_discovery_reason)

        deep_specs = []
        for i, addr, _talk, tcbl_addr, _strbuf, _wa, _pa in selected:
            deep_specs.append({"id": f"tcbl_{i}", "addr": tcbl_addr, "length": 0x40})
        deep = await reader.read_batch_snapshot(deep_specs)
        deep_results = deep.get("results", {})
        preliminary: list[ResolvedDialogueObjects] = []
        for i, addr, talk, tcbl_addr, active_strbuf, work_addr, parent in selected:
            tcbl = _item_bytes(deep_results.get(f"tcbl_{i}"))
            preliminary.append(_validate_talk_block(
                addr, talk, int(active_strbuf), tcbl_addr, tcbl,
                script_work_addr=work_addr, parent_actor_addr=parent,
            ))
        preliminary.sort(key=lambda x: x.score, reverse=True)
        best = preliminary[0]
        if best.score < 140 or not _valid_ptr(best.bitmap_addr, aligned=4):
            self.invalidate()
            return best

        bitmap_payload = await reader.read_batch_snapshot([
            {"id": "bitmap", "addr": int(best.bitmap_addr), "length": 0x20},
        ])
        bitmap = _item_bytes((bitmap_payload.get("results") or {}).get("bitmap"))
        # Re-read the selected talk/TCBL together with Bitmap to avoid accepting
        # a candidate whose owner changed during discovery.
        final_payload = await reader.read_batch_snapshot([
            {"id": "dialogue_live_talk", "addr": int(best.talkmsgwin_addr), "length": 0xB0},
            {"id": "dialogue_live_tcbl", "addr": int(best.tcbl_addr), "length": 0x40},
            {"id": "dialogue_live_bitmap", "addr": int(best.bitmap_addr), "length": 0x20},
        ])
        fr = final_payload.get("results", {})
        final = _validate_talk_block(
            int(best.talkmsgwin_addr), _item_bytes(fr.get("dialogue_live_talk")), int(best.strbuf_addr),
            int(best.tcbl_addr), _item_bytes(fr.get("dialogue_live_tcbl")),
            _item_bytes(fr.get("dialogue_live_bitmap")) or bitmap,
            script_work_addr=best.script_work_addr, parent_actor_addr=best.parent_actor_addr,
        )
        if final.valid:
            self.cached = final
            self.discovery_count += 1
            self.last_discovery_reason = final.disambiguation_reason
        else:
            self.invalidate()
        return final

    @staticmethod
    def parent_actor_from_batch(batch_results: Dict[str, Any], resolved: ResolvedDialogueObjects) -> Dict[str, Any]:
        return _actor_summary(batch_results.get("dialogue_parent_actor"), resolved.parent_actor_addr)


def resolve_from_ram(ram: bytes) -> ResolvedDialogueObjects:
    """Resolve one saved 4 MiB Main RAM image for offline evidence regression."""
    if len(ram) < 0x400000:
        return ResolvedDialogueObjects(disambiguation_reason="Main_RAM_dump_truncated")
    # Find the ScriptWork in its verified current-ROM allocation neighborhood.
    sw_start, sw_end = 0x247400, min(len(ram), 0x247C00)
    sw_data = ram[sw_start:sw_end]
    sw_item = {"offset": sw_start, "bytes": list(sw_data)}
    work_addr, active_strbuf, parent = _script_work_from_context(sw_item)
    if not _valid_ptr(active_strbuf, aligned=4):
        return ResolvedDialogueObjects(disambiguation_reason="offline_ScriptWork_StrBuf_unresolved")
    best = ResolvedDialogueObjects(script_work_addr=work_addr, strbuf_addr=active_strbuf, parent_actor_addr=parent)
    start, end = TALK_SCAN_START, min(len(ram), TALK_SCAN_START + TALK_SCAN_SIZE)
    cursor = start
    while True:
        pos = ram.find(TALK_TAG, cursor, end)
        if pos < 0:
            break
        talk_addr = ARM9_BASE + pos
        talk = ram[pos:pos + 0xB0]
        if _u32(talk, TALK_STRBUF) == active_strbuf:
            phase_ptr = _u32(talk, TALK_PHASE_PTR)
            if _valid_ptr(phase_ptr, aligned=4):
                tcbl_addr = int(phase_ptr) - TCBL_PHASE
                tpos = tcbl_addr - ARM9_BASE
                tcbl = ram[tpos:tpos + 0x40]
                bitmap_addr = _u32(tcbl, TCBL_BITMAP)
                bitmap = b""
                if _valid_ptr(bitmap_addr, aligned=4):
                    bpos = int(bitmap_addr) - ARM9_BASE
                    bitmap = ram[bpos:bpos + 0x20]
                cand = _validate_talk_block(
                    talk_addr, talk, int(active_strbuf), tcbl_addr, tcbl, bitmap,
                    script_work_addr=work_addr, parent_actor_addr=parent,
                )
                if cand.score > best.score:
                    best = cand
        cursor = pos + 1
    return best
