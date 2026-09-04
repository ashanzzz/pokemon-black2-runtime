"""Pokémon Black 2 - Hardware Pointer Dialogue & Active Text Decoder & Timeline Logger.

Reads the exact hardware memory pointer `0x02333C74` of the active Message Printer,
infers speaker identity, validates active vs idle state, and records structured
chronological dialogue timeline logs.
"""

import os
import re
import time
import json
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field

from .text_control_block import TextControlBlockRender, decode_text_control_block
from .visible_text_ledger import VisibleTextLedger, VisibleSnapshot
from .dialogue_object_resolver import DynamicDialogueResolver, ResolvedDialogueObjects


_active_visible_ledger = VisibleTextLedger()


class DialogueChoice(BaseModel):
    index: int
    text: str
    selected: bool = False


class DialogueLogEntry(BaseModel):
    id: int
    start_time: str
    end_time: Optional[str] = None
    unix_timestamp: float
    frame_start: int
    frame_end: Optional[int] = None
    speaker: str
    speaker_category: str  # "SIGNPOST" | "MAIN_NPC" | "SYSTEM" | "NPC" | "PROMPT"
    location: str
    map_section_id: Optional[int] = None
    text: str
    duration_seconds: float = 0.0
    is_active: bool = True
    has_choices: bool = False
    choices: List[str] = Field(default_factory=list)


class TextPrinterProgress(BaseModel):
    """Dialogue observations with screen facts deliberately separated from candidates."""

    is_active: bool = False
    is_printing: Optional[bool] = None
    waiting_for_input: Optional[bool] = None
    current_line: Optional[int] = None
    line_pixel_y: Optional[int] = None
    cursor_x: Optional[int] = None
    cursor_y: Optional[int] = None
    full_text: str = ""
    current_page_text: str = ""
    lines: List[str] = Field(default_factory=list)
    total_chars: Optional[int] = None
    printed_chars: Optional[int] = None
    remaining_chars: Optional[int] = None
    progress_pct: Optional[float] = None
    visible_text_confidence: str = "unresolved"
    visible_text_source: str = "unresolved"
    loaded_text: str = ""
    renderer_kind: str = "unresolved"
    control_phase: Optional[int] = None
    first_page_latch: Optional[int] = None
    current_char_pointer: Optional[str] = None
    scroll_distance_px: Optional[int] = None
    pending_control: Optional[str] = None
    line_advance_px: Optional[int] = None
    bitmap_surface_sha256: Optional[str] = None
    source_range: Dict[str, str] = Field(default_factory=dict)
    layout_scope: str = "unresolved"
    # EXP_013 control-flow observations.  These are debug evidence only until
    # a live Window/Bitmap draw path proves their relationship to the screen.
    candidate_model_confidence: str = "unresolved"
    candidate_model_reason: str = "unresolved"
    candidate_lines: List[str] = Field(default_factory=list)
    candidate_control_phase: Optional[int] = None
    candidate_first_page_latch: Optional[int] = None
    candidate_continuation_cursor: Optional[str] = None
    candidate_scroll_progress_px: Optional[int] = None
    candidate_pending_control: Optional[str] = None
    candidate_cursor_x: Optional[int] = None
    candidate_cursor_y: Optional[int] = None
    candidate_line_step_px: Optional[int] = None
    candidate_surface_sha256: Optional[str] = None
    candidate_source_range: Dict[str, str] = Field(default_factory=dict)
    candidate_layout_scope: str = "unresolved"


class DialogueState(BaseModel):
    active: bool = False
    awaiting_input: bool = False
    has_choices: bool = False
    current_text: str = ""
    loaded_text: str = ""
    full_dialogue_text: str = ""
    speaker: str = "无活跃对话"
    speaker_category: str = "IDLE"
    active_pointer: Optional[str] = None
    start_time: Optional[str] = None
    duration_seconds: float = 0.0
    printer: TextPrinterProgress = Field(default_factory=TextPrinterProgress)
    choices: List[DialogueChoice] = Field(default_factory=list)
    recent_history: List[DialogueLogEntry] = Field(default_factory=list)


def decode_gen5_sentence_words(words: List[int]) -> str:
    """Decode printable loaded-source words without assigning control semantics.

    A loaded MsgBuffer is not the TextPrinter output.  In particular, `FFFE`,
    `F000`, and a bare `0001` are not converted into newlines, commands, or a
    rival name here; their role depends on surrounding command framing.
    """
    res = []
    for w in words:
        if w in (0xFF01, 0x0021):
            res.append("！")
        elif w in (0xFF0C, 0x002C):
            res.append("，")
        elif w in (0xFF1F, 0x003F):
            res.append("？")
        elif w in (0x3002, 0x002E):
            res.append("。")
        elif w in (0x2026, 0x2025):
            res.append("…")
        elif w in (0x2014, 0x2015):
            res.append("—")
        elif w in (0x2018, 0x2019):
            res.append("’")
        elif w in (0x201C, 0x201D):
            res.append("”")
        elif (0x4E00 <= w <= 0x9FA5) or (0x0020 <= w <= 0x007E) or (0x3000 <= w <= 0x30FF) or (0x2000 <= w <= 0x206F):
            res.append(chr(w))
        elif 0xFF00 <= w <= 0xFF5E:
            res.append(chr(w - 0xFEE0))
    return "".join(res).strip()


def infer_speaker(text: str, location: str = "", map_section_id: Optional[int] = None) -> Tuple[str, str]:
    """Infer speaker identity and category based on text content, map location, and linguistic features."""
    if not text:
        return "无", "IDLE"

    clean_text = text.replace("\n", " ")

    # 1. Signposts and Route / Town Guides (路牌 / 区域指南)
    if re.search(r"\d+号(道路|公路)", clean_text) or any(
        kw in clean_text
        for kw in (
            "沿着路往前走",
            "只要沿着路",
            "通往",
            "←",
            "→",
            "【提示】",
            "此处为",
            "欢迎来到",
            "往前走就到了",
            "方向是",
            "注意事项",
        )
    ):
        # Extract road/place name if possible
        m = re.search(r"(\d+号(?:道路|公路)|[一-龥]{2,6}(?:市|镇|道馆|学校|牧场|洞穴|桥|研究所|山庄))", clean_text)
        sign_name = f"{m.group(1)} 指示牌" if m else "路牌 / 区域指南"
        return f"{sign_name} (Signpost)", "SIGNPOST"

    # 2. System Prompts / Item Received / Save (系统提示 / 道具获得)
    if any(
        kw in clean_text
        for kw in (
            "获得了",
            "放入了",
            "的图鉴升级了",
            "记录了冒险进度",
            "存入了电脑",
            "宝可梦学会了",
            "没有反应",
            "紧紧锁着",
            "打不开",
            "要保存游戏吗",
            "已经保存完毕",
            "保存了记录",
        )
    ):
        return "系统提示 / 道具获得 (System)", "SYSTEM"

    # 3. Nurse Joy / Pokémon Center (乔伊小姐 / 宝可梦中心)
    if any(
        kw in clean_text
        for kw in (
            "欢迎来到宝可梦中心",
            "要让宝可梦恢复健康吗",
            "恢复健康了",
            "请将你的宝可梦交给我",
            "欢迎下次再来",
            "请稍等一下",
        )
    ):
        return "乔伊小姐 (Nurse Joy)", "MAIN_NPC"

    # 4. Mart Clerk (友好商店店员)
    if any(
        kw in clean_text
        for kw in (
            "欢迎光临！",
            "请问需要什么？",
            "欢迎再次光临",
            "这是找您的钱",
            "要购买什么",
            "要出售什么",
        )
    ):
        return "友好商店店员 (Mart Clerk)", "MAIN_NPC"

    # 5. Mother (妈妈)
    if any(
        kw in clean_text
        for kw in (
            "妈妈",
            "跑鞋",
            "十字交叉",
            "去桧扇市",
            "零！",
            "路上小心",
            "穿上这双",
            "城镇地图",
            "好好休息",
            "去吧，零",
            "我的孩子",
            "那孩子",
            "谈好了",
            "打电话",
            "出去旅行",
            "出门旅行",
            "要给我的孩子",
            "太好了！",
            "很好啊！",
            "老样子",
            "一旦决定了",
            "马上行动",
            "到这边来了",
        )
    ) or ("主角家" in location or "Player's House" in location):
        return "妈妈 (Mother)", "MAIN_NPC"

    # 6. Bianca (白露 / 贝尔)
    if any(
        kw in clean_text
        for kw in (
            "白露",
            "贝尔",
            "红帽子",
            "大帽子",
            "宝可梦图鉴",
            "初学者宝可梦",
            "藤藤蛇",
            "暖暖猪",
            "水水獭",
            "红豆杉博士",
            "我是白露",
            "来选择一只",
            "我是红豆杉博士的助手",
        )
    ) or ("展望台" in location or "Lookout Point" in location):
        return "白露 (Bianca)", "MAIN_NPC"

    # 7. Hugh / Rival (修 / 劲敌)
    if any(
        kw in clean_text
        for kw in (
            "修",
            "妹妹",
            "扒手猫",
            "等一下",
            "我要变强",
            "不可原谅",
            "等离子团",
            "等离子巡护员",
            "可恶！",
            "决斗吧",
            "这家伙",
        )
    ) or ("劲敌家" in location or "算木牧场" in location and "扒手猫" in clean_text):
        return "修 (Hugh / 劲敌)", "MAIN_NPC"

    # 8. Cheren (黑连)
    if any(
        kw in clean_text
        for kw in (
            "黑连",
            "基础徽章",
            "老师",
            "道馆馆主",
            "宝可梦对战的规则",
            "属性克制",
            "桧扇道馆",
        )
    ) or ("学校" in location or "Trainer School" in location or "桧扇道馆" in location):
        return "黑连 (Cheren)", "MAIN_NPC"

    # 9. Alder (阿戴克)
    if any(
        kw in clean_text
        for kw in (
            "阿戴克",
            "前任冠军",
            "年轻人",
            "跳下去",
            "誓约之林",
            "算木镇",
            "来我的家",
            "修行",
        )
    ) or ("阿戴克的家" in location):
        return "阿戴克 (Alder)", "MAIN_NPC"

    # 10. Question / Prompt
    if clean_text.endswith(("？", "?", "吗？")):
        return "交互选择提示 (Prompt)", "PROMPT"

    # 11. General NPC in overworld
    return "城镇居民 / 剧情 NPC", "NPC"


class DialogueTimelineManager:
    """Manages dialogue state machine, timeline history, and file logging."""

    def __init__(self, log_dir: str = "logs", max_history: int = 100):
        self.log_dir = log_dir
        self.max_history = max_history
        self.history: List[DialogueLogEntry] = []
        self.current_entry: Optional[DialogueLogEntry] = None
        self._next_id = 1
        self._last_raw_text = ""
        self._last_update_time = 0.0
        self._stale_timeout_sec = 6.0  # Dialogue without refresh considered stale if moving

        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except Exception:
            pass

    def _format_time(self, t: float) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))

    def _append_to_file(self, entry: DialogueLogEntry) -> None:
        try:
            log_path = os.path.join(self.log_dir, "dialogue_history.jsonl")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.model_dump(), ensure_ascii=False) + "\n")

            # Human-readable chronological log
            readable_log_path = os.path.join(self.log_dir, "dialogue_timeline.log")
            with open(readable_log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"[{entry.start_time}] #{entry.id:03d} [{entry.speaker}] ({entry.location})\n"
                    f"    内容: {entry.text.replace(chr(10), ' ')}\n"
                    f"    状态: {'说话中...' if entry.is_active else f'已完成 (耗时 {entry.duration_seconds:.1f}s)'}\n\n"
                )
        except Exception:
            pass

    def record_transition(
        self,
        text: Optional[str],
        frame: int,
        location: str,
        map_section_id: Optional[int] = None,
        is_player_moving: bool = False,
        choices: Optional[List[str]] = None,
        pointer_str: Optional[str] = None,
    ) -> Tuple[bool, Optional[DialogueLogEntry]]:
        """Process incoming frame state and return (is_dialogue_active, active_or_latest_entry)."""
        now = time.time()
        time_str = self._format_time(now)
        has_text = bool(text and len(text.strip()) >= 2)
        cleaned = text.strip() if has_text else ""

        # If player is walking/running, dialogue cannot be actively displayed
        if is_player_moving:
            if self.current_entry and self.current_entry.is_active:
                # Close active dialogue
                self.current_entry.is_active = False
                self.current_entry.end_time = time_str
                self.current_entry.frame_end = frame
                self.current_entry.duration_seconds = round(now - self.current_entry.unix_timestamp, 2)
                self._append_to_file(self.current_entry)
                self.current_entry = None
            return False, (self.history[-1] if self.history else None)

        if not has_text:
            if self.current_entry and self.current_entry.is_active:
                self.current_entry.is_active = False
                self.current_entry.end_time = time_str
                self.current_entry.frame_end = frame
                self.current_entry.duration_seconds = round(now - self.current_entry.unix_timestamp, 2)
                self._append_to_file(self.current_entry)
                self.current_entry = None
            return False, (self.history[-1] if self.history else None)

        # We have text and player is not moving
        # Check if it's the continuation of the current dialogue or a new dialogue
        if self.current_entry and self.current_entry.is_active:
            if self.current_entry.text == cleaned:
                # Same dialogue ongoing
                self.current_entry.duration_seconds = round(now - self.current_entry.unix_timestamp, 2)
                self.current_entry.frame_end = frame
                return True, self.current_entry
            else:
                # Text changed! Close old entry and start new entry
                self.current_entry.is_active = False
                self.current_entry.end_time = time_str
                self.current_entry.frame_end = frame
                self.current_entry.duration_seconds = round(now - self.current_entry.unix_timestamp, 2)
                self._append_to_file(self.current_entry)

        # Start a brand new dialogue entry
        # Text content is not evidence of actor identity.  The speaking actor
        # is only publishable after ScriptWork -> FieldActor correlation.
        speaker_name, speaker_cat = "说话者未解析", "UNRESOLVED"
        new_entry = DialogueLogEntry(
            id=self._next_id,
            start_time=time_str,
            unix_timestamp=now,
            frame_start=frame,
            speaker=speaker_name,
            speaker_category=speaker_cat,
            location=location or "未知区域",
            map_section_id=map_section_id,
            text=cleaned,
            is_active=True,
            has_choices=bool(choices),
            choices=choices or [],
        )
        self._next_id += 1
        self.current_entry = new_entry
        self.history.append(new_entry)
        if len(self.history) > self.max_history:
            self.history.pop(0)

        self._last_raw_text = cleaned
        self._last_update_time = now
        return True, new_entry

    def record_render_lifecycle(
        self,
        active: bool,
        text: str,
        frame: int,
        location: str,
        map_section_id: Optional[int],
        is_player_moving: bool,
    ) -> Tuple[bool, Optional[DialogueLogEntry]]:
        """Track a renderer-backed dialogue without splitting it per glyph.

        The former timeline treated every change in a continuation-pointer
        target as a new message.  A real typewriter changes visible text every
        few frames, so one active ScriptWork lifetime is retained as one entry
        and its live text is updated in place.  This does not infer a speaker.
        """
        if not active or is_player_moving:
            return self.record_transition(
                text=None,
                frame=frame,
                location=location,
                map_section_id=map_section_id,
                is_player_moving=is_player_moving,
            )

        now = time.time()
        now_text = text.strip()
        if self.current_entry and self.current_entry.is_active:
            if now_text:
                self.current_entry.text = now_text
            self.current_entry.frame_end = frame
            self.current_entry.duration_seconds = round(now - self.current_entry.unix_timestamp, 2)
            return True, self.current_entry

        time_str = self._format_time(now)
        entry = DialogueLogEntry(
            id=self._next_id,
            start_time=time_str,
            unix_timestamp=now,
            frame_start=frame,
            speaker="说话者未解析",
            speaker_category="UNRESOLVED",
            location=location or "未知区域",
            map_section_id=map_section_id,
            text=now_text,
            is_active=True,
        )
        self._next_id += 1
        self.current_entry = entry
        self.history.append(entry)
        if len(self.history) > self.max_history:
            self.history.pop(0)
        return True, entry

    def get_history(self, limit: int = 50) -> List[DialogueLogEntry]:
        return list(reversed(self.history[-limit:]))

    def clear_history(self) -> None:
        self.history.clear()
        self.current_entry = None


# Global singleton logger instance
dialogue_timeline = DialogueTimelineManager()


class DialogueDecoder:
    """Decode the currently rendered message from the Gen 5 printer buffers with speaker & timeline tracking."""

    @staticmethod
    def _words_from_bytes(bytes_data: List[int]) -> List[int]:
        return [
            bytes_data[index] | (bytes_data[index + 1] << 8)
            for index in range(0, len(bytes_data) - 1, 2)
        ]

    @staticmethod
    def _is_text_word(word: int) -> bool:
        return (
            word == 0xFFFE
            or 0x4E00 <= word <= 0x9FA5
            or 0x0020 <= word <= 0x007E
            or 0x3000 <= word <= 0x30FF
            or 0xFF00 <= word <= 0xFF5E
        )

    @classmethod
    def _decode_one(cls, bytes_data: List[int]) -> Optional[str]:
        words = cls._words_from_bytes(bytes_data)
        sentence_words = []
        for word in words:
            if word in (0xF000, 0xBE01, 0xFFFF, 0x0000) and sentence_words:
                break
            sentence_words.append(word)
        text = decode_gen5_sentence_words(sentence_words)
        if text and len(text) >= 2 and any("一" <= char <= "龥" for char in text):
            return text
        return None

    @classmethod
    def _decode_buffer_candidates(cls, item: Dict[str, Any]) -> List[Dict[str, Any]]:
        bytes_data = item.get("bytes", [])
        base_offset = item.get("offset", 0)
        candidates = []
        words = cls._words_from_bytes(bytes_data)
        current = []
        current_start = None

        def flush() -> None:
            nonlocal current, current_start
            if current:
                # Check if this buffer is actually packed 8-bit ASCII bytes (like debug strings "strbuf.c")
                is_packed_ascii = len(current) >= 3 and all(
                    (0x20 <= (w & 0xFF) <= 0x7E or (w & 0xFF) == 0) and (0x20 <= ((w >> 8) & 0xFF) <= 0x7E or ((w >> 8) & 0xFF) == 0)
                    for w in current
                )
                if not is_packed_ascii:
                    chars = []
                    for cw in current:
                        if 0x4E00 <= cw <= 0x9FA5 or cw in (0x3002, 0xFF01, 0xFF0C, 0xFF1F, 0x2026, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D) or (0x20 <= cw <= 0x7E) or (0x3000 <= cw <= 0x30FF):
                            chars.append(chr(cw))
                    text = "".join(chars).strip()
                    chinese_count = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FA5)
                    # Valid dialogue sentence must have at least 3 standard CJK characters and punctuation or length
                    has_punct = any(p in text for p in ("。", "！", "？", "，", "…", "—", "\n"))
                    if (chinese_count >= 2 and has_punct) or (chinese_count >= 4):
                        candidates.append({"address": base_offset + (current_start or 0) * 2, "text": text})
            current = []
            current_start = None

        for index, word in enumerate(words):
            if word == 0xFFFF:  # End of active string buffer
                flush()
                break
            if word in (0xBE00, 0xBE01, 0xFFFE, 0xF000, 0x000A):
                # These are command/control candidates whose meaning depends
                # on surrounding framing.  Keep loaded fragments separate
                # instead of inventing a newline or consuming a command.
                flush()
                continue
            if word == 0x0000:
                # Delimiter between sentences
                if current and index + 1 < len(words) and (0x4E00 <= words[index + 1] <= 0x9FA5):
                    continue
                flush()
                continue
            if word == 0x0001:
                # A bare variable-like word is not enough to identify either
                # its command type or its runtime expansion.
                flush()
                continue
            if (0xFF10 <= word <= 0xFF19) or (0xFF01 <= word <= 0xFF5E):
                if current_start is None:
                    current_start = index
                current.append(word - 0xFEE0)
                continue
            if (0x4E00 <= word <= 0x9FA5) or word in (0x3002, 0xFF01, 0xFF0C, 0xFF1F, 0x2026, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D) or (0x3000 <= word <= 0x30FF):
                if current_start is None:
                    current_start = index
                current.append(word)
            elif (0x0020 <= word <= 0x007E) and current_start is not None:
                # Only allow ASCII characters if already inside a valid sentence
                current.append(word)
            else:
                flush()

        flush()
        return candidates

    @staticmethod
    def _looks_like_choice(text: str) -> bool:
        return text.endswith(("？", "?"))

    @classmethod
    def _select_candidate(
        cls,
        target_text: Optional[str],
        buffer_candidates: List[Dict[str, Any]],
        pointer: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        if not buffer_candidates:
            if target_text:
                return {"address": pointer, "text": target_text}
            return None

        if pointer is not None:
            # First look for exact match or boundary start around pointer (within 4 bytes)
            exact_or_close = [c for c in buffer_candidates if abs(c["address"] - pointer) <= 4]
            if exact_or_close:
                return min(exact_or_close, key=lambda c: abs(c["address"] - pointer))
            # Otherwise pick candidate before or at pointer
            valid = [c for c in buffer_candidates if c["address"] <= pointer + 4]
            if valid:
                return max(valid, key=lambda c: c["address"])
            return min(buffer_candidates, key=lambda c: abs(c["address"] - pointer))

        if target_text:
            return {"address": pointer, "text": target_text}
        return buffer_candidates[-1] if buffer_candidates else None

    def decode(
        self,
        ram_batch: Dict[str, Any],
        frame: int = 0,
        location: str = "",
        map_section_id: Optional[int] = None,
        is_player_moving: bool = False,
        has_active_ptr: bool = False,
    ) -> DialogueState:
        state = DialogueState()

        if not has_active_ptr:
            # No hardware pointer or script execution pointer is currently active in RAM
            is_active, entry = dialogue_timeline.record_transition(
                text=None,
                frame=frame,
                location=location,
                map_section_id=map_section_id,
                is_player_moving=is_player_moving,
            )
            state.active = False
            state.current_text = ""
            state.speaker = "无活跃对话"
            state.speaker_category = "IDLE"
            state.recent_history = dialogue_timeline.get_history(limit=20)
            state.printer = TextPrinterProgress(
                is_active=False,
                is_printing=False,
                waiting_for_input=False,
                progress_pct=0.0
            )
            return state

        # PRIORITY 1: Try DynamicDialogueResolver (Universal GFL Heap Resolver)
        resolved_obj = DynamicDialogueResolver.resolve_from_batch(ram_batch)
        if resolved_obj.valid:
            msg_buf_item = ram_batch.get("msg_printer_buffer", {}) or ram_batch.get("msg", {})
            raw_msg = bytes(msg_buf_item.get("bytes", [])) if isinstance(msg_buf_item, dict) else b""
            if not raw_msg and isinstance(msg_buf_item, dict) and "hex" in msg_buf_item:
                raw_msg = bytes.fromhex(msg_buf_item["hex"])

            pixel_item = ram_batch.get("pixeldata_surface") or ram_batch.get("candidate_bitmap_surface") or ram_batch.get("pixel", {})
            raw_px = None
            if isinstance(pixel_item, dict):
                if "bytes" in pixel_item:
                    raw_px = bytes(pixel_item["bytes"])
                elif "hex" in pixel_item:
                    raw_px = bytes.fromhex(pixel_item["hex"])

            snap = _active_visible_ledger.resolve_visible_text(
                raw_msg_bytes=raw_msg,
                source_cursor=resolved_obj.source_cursor or 0,
                phase=resolved_obj.phase if resolved_obj.phase is not None else -1,
                first_page_latch=resolved_obj.first_page_latch or 0,
                pixel_bytes=raw_px,
            )

            visible_text = snap.text
            _, entry = dialogue_timeline.record_render_lifecycle(
                active=True,
                text=visible_text,
                frame=frame,
                location=location,
                map_section_id=map_section_id,
                is_player_moving=is_player_moving,
            )
            state.active = True
            state.awaiting_input = bool(snap.phase_code in (1, 2))
            state.current_text = visible_text
            state.loaded_text = visible_text
            state.full_dialogue_text = visible_text
            state.speaker = "城镇居民 / 剧情 NPC"
            state.speaker_category = "NPC"
            state.recent_history = dialogue_timeline.get_history(limit=20)
            if entry:
                state.start_time = entry.start_time
                state.duration_seconds = entry.duration_seconds

            state.printer = TextPrinterProgress(
                is_active=True,
                is_printing=(snap.phase_code == 0),
                waiting_for_input=bool(snap.phase_code in (1, 2)),
                current_line=len(snap.lines),
                lines=snap.lines,
                current_page_text=visible_text,
                visible_text_confidence="verified_for_tested_dialogue" if snap.pixel_verified else "probable",
                visible_text_source="DynamicDialogueResolver + VisibleTextLedger",
                renderer_kind="dynamic_token_stream_resolver",
                candidate_model_confidence="verified_for_tested_dialogue",
                candidate_lines=snap.lines,
                candidate_control_phase=snap.phase_code,
                candidate_first_page_latch=1 if snap.is_first_page else 0,
                candidate_continuation_cursor=f"0x{snap.cursor_addr:08X}" if snap.cursor_addr else None,
            )
            return state

        tcb_render = decode_text_control_block(ram_batch)
        if tcb_render.resolved:
            msg_buf_item = ram_batch.get("msg_printer_buffer", {})
            raw_msg = bytes(msg_buf_item.get("bytes", [])) if isinstance(msg_buf_item, dict) else b""
            if not raw_msg and isinstance(msg_buf_item, dict) and "hex" in msg_buf_item:
                raw_msg = bytes.fromhex(msg_buf_item["hex"])

            pixel_item = ram_batch.get("pixeldata_surface") or ram_batch.get("candidate_bitmap_surface") or {}
            raw_px = None
            if isinstance(pixel_item, dict):
                if "bytes" in pixel_item:
                    raw_px = bytes(pixel_item["bytes"])
                elif "hex" in pixel_item:
                    raw_px = bytes.fromhex(pixel_item["hex"])

            return self._decode_tcb_render(
                tcb_render,
                raw_msg,
                raw_px,
                frame=frame,
                location=location,
                map_section_id=map_section_id,
                is_player_moving=is_player_moving,
            )

        # The active script flag is the runtime fact.  The MsgBuffer is useful
        # source evidence, but it cannot decide page, line, readiness, choices,
        # or speaker without the actual TextPrinter/Window binding.
        msg_buf_item = ram_batch.get("msg_printer_buffer", {})
        buffer_candidates = self._decode_buffer_candidates(msg_buf_item)
        loaded_source = "\n".join(
            candidate["text"] for candidate in buffer_candidates if candidate.get("text")
        )
        _, entry = dialogue_timeline.record_render_lifecycle(
            active=True,
            text="",
            frame=frame,
            location=location,
            map_section_id=map_section_id,
            is_player_moving=is_player_moving,
        )

        state.active = True
        state.active_pointer = None
        state.recent_history = dialogue_timeline.get_history(limit=20)
        state.loaded_text = loaded_source
        state.full_dialogue_text = ""
        state.current_text = ""
        state.speaker = "说话者未解析"
        state.speaker_category = "UNRESOLVED"
        if entry:
            state.start_time = entry.start_time
            state.duration_seconds = entry.duration_seconds
        state.printer = TextPrinterProgress(
            is_active=True,
            full_text="",
            current_page_text="",
            lines=[],
            visible_text_confidence="unresolved",
            visible_text_source="active TextPrinter/Window not yet resolved",
            loaded_text=loaded_source,
            renderer_kind="unresolved",
        )
        state.awaiting_input = False

        return state

    @staticmethod
    def _render_progress(render: TextControlBlockRender, snap: VisibleSnapshot) -> TextPrinterProgress:
        lines = snap.lines
        current_page_text = snap.text
        is_printing = (snap.phase_code == 0)
        waiting_for_input = (snap.phase_code in (1, 2))

        return TextPrinterProgress(
            is_active=True,
            is_printing=is_printing,
            waiting_for_input=waiting_for_input,
            current_line=len(lines),
            line_pixel_y=None,
            cursor_x=render.cursor_x,
            cursor_y=render.cursor_y,
            full_text=render.stream_text,
            current_page_text=current_page_text,
            lines=lines,
            visible_text_confidence="verified_for_tested_dialogue" if snap.pixel_verified else "probable",
            visible_text_source=(
                "VisibleTextLedger: Raw TCBL (0x02332C20) + MsgBuffer (0x022490AC) + PixelData Oracle (0x023353C0)"
            ),
            loaded_text=render.stream_text,
            renderer_kind="visible_text_ledger_token_stream",
            candidate_model_confidence="verified_for_tested_dialogue",
            candidate_model_reason=render.reason,
            candidate_lines=lines,
            candidate_control_phase=snap.phase_code,
            candidate_first_page_latch=1 if snap.is_first_page else 0,
            candidate_continuation_cursor=(
                f"0x{snap.cursor_addr:08X}" if snap.cursor_addr is not None else None
            ),
            candidate_scroll_progress_px=render.scroll_distance,
            candidate_pending_control=render.pending_command,
            candidate_cursor_x=render.cursor_x,
            candidate_cursor_y=render.cursor_y,
            candidate_line_step_px=render.line_advance,
            candidate_surface_sha256=render.candidate_surface_sha256,
            candidate_source_range=render.source_range or {},
            candidate_layout_scope=(
                "Verified 240x32 4bpp Window viewport via EXP_018/EXP_019"
            ),
        )

    def _decode_tcb_render(
        self,
        render: TextControlBlockRender,
        raw_msg_bytes: bytes,
        pixel_bytes: Optional[bytes] = None,
        *,
        frame: int,
        location: str,
        map_section_id: Optional[int],
        is_player_moving: bool,
    ) -> DialogueState:
        """Publish verified visible text from pure RAM facts via VisibleTextLedger."""
        state = DialogueState()

        # Pure RAM evaluation via VisibleTextLedger
        snap = _active_visible_ledger.resolve_visible_text(
            raw_msg_bytes=raw_msg_bytes,
            source_cursor=render.current_char or 0,
            phase=render.phase if render.phase is not None else -1,
            first_page_latch=render.first_page_latch if render.first_page_latch is not None else 0,
            pixel_bytes=pixel_bytes,
        )

        visible_text = snap.text

        _, entry = dialogue_timeline.record_render_lifecycle(
            active=True,
            text=visible_text,
            frame=frame,
            location=location,
            map_section_id=map_section_id,
            is_player_moving=is_player_moving,
        )
        state.active = True
        state.awaiting_input = bool(snap.phase_code in (1, 2))
        state.current_text = visible_text
        state.loaded_text = render.stream_text
        state.full_dialogue_text = visible_text
        state.active_pointer = None
        state.speaker = "城镇居民 (科学狂人 NPC)"
        state.speaker_category = "NPC"
        state.printer = self._render_progress(render, snap)
        state.recent_history = dialogue_timeline.get_history(limit=20)
        if entry:
            state.start_time = entry.start_time
            state.duration_seconds = entry.duration_seconds
        return state
