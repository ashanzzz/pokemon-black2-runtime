import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.black2.runtime.control_log import RuntimeControlLog, RUNTIME_MONITOR_VERSION
from backend.black2.runtime.hub import RuntimeHub


class TestRuntimeMonitor(unittest.TestCase):
    def test_lifecycle_journal_is_persistent_and_filters_payload_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = RuntimeControlLog(Path(folder) / "runtime_control.jsonl")
            first = journal.record(
                "runtime_launcher_start",
                "launching",
                http_port=8765,
                memory="must-not-be-written",
                restart_token="must-not-be-written",
            )
            journal.record("backend_startup", "ready", bridge_port=8766)
            entries = journal.recent(10)

        self.assertEqual(RUNTIME_MONITOR_VERSION, "13.0.0")
        self.assertEqual(first["component"], "runtime-monitor")
        self.assertNotIn("memory", first["details"])
        self.assertNotIn("restart_token", first["details"])
        self.assertEqual([entry["operation"] for entry in entries], ["backend_startup", "runtime_launcher_start"])
        self.assertEqual(entries[0]["details"]["bridge_port"], 8766)

    def test_runtime_hub_publishes_cached_popup_state_without_native_handles(self):
        popup = {
            "present": True,
            "blocking": True,
            "kind": "savestate_sync_settings_mismatch",
            "title": "Savestate sync settings mismatch",
            "text": "Loadstate cancelled",
            "api_dismissible": True,
            "_window_handle": 123,
            "_button_handle": 456,
        }
        client = SimpleNamespace(is_connected=False)
        transport = SimpleNamespace(last_heartbeat=0, last_frame=10, bridge_version="1.9.0", session_id="s")
        probe = SimpleNamespace(running=True, pid=789, exe_path="EmuHawk.exe", popup=popup)
        hub = RuntimeHub(
            client=client,
            reader=None,
            state_engine=None,
            transport=transport,
            process_probe=lambda: probe,
        )

        status = hub.popup_status()

        self.assertEqual(status["status"], "blocked")
        self.assertTrue(status["emulation_blocked_by_popup"])
        self.assertEqual(status["popup"]["kind"], "savestate_sync_settings_mismatch")
        self.assertNotIn("_window_handle", status["popup"])
        self.assertNotIn("_button_handle", status["popup"])
