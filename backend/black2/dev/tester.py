"""Developer & Human Manual Testing Module.

Implements Sections 31-38, 42, 82, 85 of the Observer UI Spec.
Provides Developer Input Testing, State Change Observation, Snapshot Capture & Automation Lock.
"""

import asyncio
import hashlib
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from pydantic import BaseModel

from ..bizhawk.bridge_client import BridgeClient
from ..state.engine import SemanticStateEngine
from ..observer.logger import observer_logger


class InputTestRecord(BaseModel):
    request_id: str
    button_or_touch: str
    sent_frame: int
    duration_frames: int
    backend_status: str
    state_before: str
    state_after: str
    result: str  # "PASS" | "WARN" | "FAIL"
    timestamp: str
    frame_after: int = 0
    bridge_accepted: bool = False
    queue_before: Optional[int] = None
    queue_after: Optional[int] = None
    screen_changed: Optional[bool] = None
    before_capture_url: Optional[str] = None
    after_capture_url: Optional[str] = None


class DeveloperTestWorkbench:
    def __init__(self, client: BridgeClient, state_engine: SemanticStateEngine):
        self.client = client
        self.state_engine = state_engine
        self.automation_paused: bool = False
        self.test_logs: List[InputTestRecord] = []
        self.snapshots: Dict[str, Dict[str, Any]] = {}

    def set_automation_pause(self, paused: bool) -> bool:
        self.automation_paused = paused
        status_text = "PAUSED (Human Lock Active)" if paused else "RESUMED (Active)"
        observer_logger.log_event("automation_override", f"AI / Backend Automation {status_text}")
        return self.automation_paused

    async def execute_input_test(
        self, button: str, frames: int = 8, capture_evidence: bool = False,
    ) -> InputTestRecord:
        """Inject one button and return only evidence that the bridge can prove."""
        return await self._execute_test(
            label=button,
            frames=frames,
            inject=lambda: self.client.press_buttons([button], frames=frames),
            capture_evidence=capture_evidence,
        )

    async def execute_touch_test(
        self, x: int, y: int, frames: int = 8, capture_evidence: bool = False,
    ) -> InputTestRecord:
        """Inject one touch event and return only evidence that the bridge can prove."""
        return await self._execute_test(
            label=f"Touch({x}, {y})",
            frames=frames,
            inject=lambda: self.client.touch(x, y, frames=frames),
            capture_evidence=capture_evidence,
        )

    async def capture_evidence(self, label: str = "Map Evidence") -> Dict[str, Any]:
        """Capture one real emulator frame without injecting an input."""
        safe_label = "".join(char if char.isalnum() else "_" for char in label)[:32]
        request_id = f"capture_{safe_label or 'map'}_{uuid.uuid4().hex[:6]}"
        url = await self._capture(request_id, "frame")
        return {
            "captured": url is not None,
            "capture_url": url,
            "label": label,
            "frame": int((await self.client.get_emu_state()).get("frame", 0)),
        }

    async def _execute_test(
        self,
        label: str,
        frames: int,
        inject: Callable[[], Awaitable[Dict[str, Any]]],
        capture_evidence: bool,
    ) -> InputTestRecord:
        request_id = f"dev_input_{uuid.uuid4().hex[:6]}"
        emu_before = await self.client.get_emu_state()
        sent_frame = int(emu_before.get("frame", 0))
        state_before = self._state_label(self.state_engine.current_state)
        queue_before = await self._queue_length()
        before_capture_url = await self._capture(request_id, "before") if capture_evidence else None

        receipt = await inject()
        accepted = bool(receipt.get("queued"))
        observer_logger.log_call("DEVELOPER_TEST", f"input_test(target='{label}', req={request_id})")

        frame_after, queue_after = await self._wait_for_input_completion(sent_frame, frames)
        after_capture_url = await self._capture(request_id, "after") if capture_evidence else None
        new_state = await self.state_engine.sample_once()
        state_after = self._state_label(new_state)
        screen_changed = self._screens_differ(before_capture_url, after_capture_url)

        completed = accepted and frame_after - sent_frame >= frames and queue_after == 0
        result, backend_status = self._test_outcome(completed, screen_changed, state_before, state_after)
        record = InputTestRecord(
            request_id=request_id,
            button_or_touch=label,
            sent_frame=sent_frame,
            duration_frames=frames,
            backend_status=backend_status,
            state_before=state_before,
            state_after=state_after,
            result=result,
            timestamp=time.strftime("%H:%M:%S"),
            frame_after=frame_after,
            bridge_accepted=accepted,
            queue_before=queue_before,
            queue_after=queue_after,
            screen_changed=screen_changed,
            before_capture_url=before_capture_url,
            after_capture_url=after_capture_url,
        )
        self._append_record(record)
        return record

    def _append_record(self, record: InputTestRecord) -> None:
        self.test_logs.insert(0, record)
        del self.test_logs[50:]

    @staticmethod
    def _state_label(state: Any) -> str:
        context = getattr(state, "context", None)
        screen_type = getattr(context, "screen_type", None)
        return getattr(screen_type, "value", str(screen_type or "UNKNOWN"))

    async def _queue_length(self) -> Optional[int]:
        try:
            return int((await self.client.get_input_state()).get("queue_len", 0))
        except Exception:
            return None

    async def _wait_for_input_completion(self, sent_frame: int, frames: int) -> tuple[int, Optional[int]]:
        """Wait for the requested hold to finish without assuming a fixed emulator speed."""
        deadline = asyncio.get_running_loop().time() + 1.5
        frame_after = sent_frame
        queue_after = await self._queue_length()
        while True:
            emu_after = await self.client.get_emu_state()
            frame_after = int(emu_after.get("frame", frame_after))
            queue_after = await self._queue_length()
            if frame_after - sent_frame >= frames and queue_after == 0:
                return frame_after, queue_after
            if asyncio.get_running_loop().time() >= deadline:
                return frame_after, queue_after
            await asyncio.sleep(0.05)

    async def _capture(self, request_id: str, phase: str) -> Optional[str]:
        capture_dir = self._capture_dir()
        capture_dir.mkdir(parents=True, exist_ok=True)
        name = f"{request_id}_{phase}.png"
        path = capture_dir / name
        try:
            await self.client.capture_screen(str(path))
        except Exception:
            return None
        if not path.is_file():
            return None
        return f"/api/dev/captures/{name}"

    @staticmethod
    def _screens_differ(before_url: Optional[str], after_url: Optional[str]) -> Optional[bool]:
        if not before_url or not after_url:
            return None
        capture_dir = DeveloperTestWorkbench._capture_dir()
        before = capture_dir / before_url.rsplit("/", 1)[-1]
        after = capture_dir / after_url.rsplit("/", 1)[-1]
        if not before.is_file() or not after.is_file():
            return None
        return hashlib.sha256(before.read_bytes()).digest() != hashlib.sha256(after.read_bytes()).digest()

    @staticmethod
    def _capture_dir() -> Path:
        """Use an ASCII path because the embedded Lua decoder has no UTF-8 escape support."""
        return Path(tempfile.gettempdir()) / "pokemon_black2_dev_captures"

    @staticmethod
    def _test_outcome(
        completed: bool,
        screen_changed: Optional[bool],
        state_before: str,
        state_after: str,
    ) -> tuple[str, str]:
        if not completed:
            return "FAIL", "未完成：桥接确认、帧推进或输入队列回收缺少证据。"
        if state_before != state_after:
            return "PASS", "已验证：BizHawk 已完成输入，RAM 语义状态发生可观测变化。"
        if screen_changed:
            return "WARN", "已确认：BizHawk 已完成输入；前后 PNG 不同，但动画也会改变画面，不能单独作为操作成功证据。"
        return "WARN", "已确认：BizHawk 已完成输入；当前画面未出现可判别的游戏响应。"

    async def create_test_snapshot(self, label: str = "Dev Snapshot") -> Dict[str, Any]:
        """Capture memory snapshot for reverse engineering validation."""
        emu = await self.client.get_emu_state()
        frame = emu.get("frame", 0)
        snap_id = f"snap_{int(time.time()*1000)}"
        
        # Sample 16KB from Main RAM
        bytes_data = await self.client.read_bytes(0x000000, 16384, "Main RAM")
        
        snap = {
            "id": snap_id,
            "label": label,
            "frame": frame,
            "timestamp": time.strftime("%H:%M:%S"),
            "size": len(bytes_data),
            "hex_sample": bytes_data[:32]
        }
        self.snapshots[snap_id] = snap
        observer_logger.log_event("snapshot_captured", f"Created developer memory snapshot '{label}' at frame {frame}")
        return snap


dev_workbench: Optional[DeveloperTestWorkbench] = None

def init_dev_workbench(client: BridgeClient, state_engine: SemanticStateEngine) -> DeveloperTestWorkbench:
    global dev_workbench
    dev_workbench = DeveloperTestWorkbench(client, state_engine)
    return dev_workbench
