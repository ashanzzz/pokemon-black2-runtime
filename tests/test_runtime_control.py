import unittest

from backend.black2.api.runtime_routes import RUNTIME_CONTROL_VERSION, _restart_command


class TestRuntimeControl(unittest.TestCase):
    def test_replacement_command_preserves_runtime_ports_and_delay(self):
        command = _restart_command()
        self.assertEqual(RUNTIME_CONTROL_VERSION, "8.0.0")
        self.assertTrue(command[1].endswith("run_runtime.py"))
        self.assertIn("--host", command)
        self.assertIn("--port", command)
        self.assertIn("--bridge-host", command)
        self.assertIn("--bridge-port", command)
        self.assertEqual(command[-2:], ["--start-delay", "2.0"])
