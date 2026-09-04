import tempfile
import unittest
from pathlib import Path

from backend.black2.runtime.control_log import RuntimeControlLog, RUNTIME_MONITOR_VERSION


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

        self.assertEqual(RUNTIME_MONITOR_VERSION, "5.5.2")
        self.assertEqual(first["component"], "runtime-monitor")
        self.assertNotIn("memory", first["details"])
        self.assertNotIn("restart_token", first["details"])
        self.assertEqual([entry["operation"] for entry in entries], ["backend_startup", "runtime_launcher_start"])
        self.assertEqual(entries[0]["details"]["bridge_port"], 8766)
