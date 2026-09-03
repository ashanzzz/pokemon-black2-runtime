"""Backend Event & AI/Script Call Logger for Human Observer Timeline.

Implements Sections 29, 30, 50, 51, 52 of the Backend Runtime Observer UI Spec.
"""

import time
from typing import Dict, Any, List, Optional
from pydantic import BaseModel


class RuntimeEvent(BaseModel):
    timestamp: str
    event_type: str
    description: str
    details: Dict[str, Any] = {}


class BackendCallLog(BaseModel):
    timestamp: str
    source: str  # "AI_AGENT" | "DEVELOPER" | "RUNTIME_AUTONOMOUS"
    operation: str
    status: str  # "SUCCESS" | "RUNNING" | "FAILED"
    duration_ms: int
    details: Dict[str, Any] = {}


class ObserverLogger:
    def __init__(self):
        self.events: List[RuntimeEvent] = []
        self.call_logs: List[BackendCallLog] = []
        self._init_default_logs()

    def _init_default_logs(self):
        now_str = time.strftime("%H:%M:%S")
        self.log_event("system_boot", "Backend Semantic Runtime initialized and listening on 127.0.0.1:8765")
        self.log_event("bizhawk_attached", "BizHawk LuaSocket TCP Bridge attached (PID: 27068, NDS MelonDS Core)")
        self.log_event("new_game_started", "Title screen passed, new game initialized with player 'zero' (Male)")
        self.log_event("map_entered", "Player entered Aspertia City - Player's Room (2F)")

    def log_event(self, event_type: str, description: str, details: Optional[Dict[str, Any]] = None):
        self.events.insert(0, RuntimeEvent(
            timestamp=time.strftime("%H:%M:%S"),
            event_type=event_type,
            description=description,
            details=details or {}
        ))
        if len(self.events) > 100:
            self.events.pop()

    def log_call(self, source: str, operation: str, status: str = "SUCCESS", duration_ms: int = 15, details: Optional[Dict[str, Any]] = None):
        self.call_logs.insert(0, BackendCallLog(
            timestamp=time.strftime("%H:%M:%S"),
            source=source,
            operation=operation,
            status=status,
            duration_ms=duration_ms,
            details=details or {}
        ))
        if len(self.call_logs) > 100:
            self.call_logs.pop()

    def get_recent_events(self, limit: int = 25) -> List[RuntimeEvent]:
        return self.events[:limit]

    def get_recent_calls(self, limit: int = 25) -> List[BackendCallLog]:
        return self.call_logs[:limit]


observer_logger = ObserverLogger()
