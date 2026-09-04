import unittest

from backend.black2.runtime.versions import (
    BIZHAWK_BRIDGE_VERSION,
    RUNTIME_RELEASE_VERSION,
    component_version_report,
)


class TestRuntimeVersions(unittest.TestCase):
    def test_connected_bridge_is_compared_to_required_version(self):
        report = component_version_report(
            bridge_version=BIZHAWK_BRIDGE_VERSION,
            bridge_connected=True,
        )
        by_id = {item["id"]: item for item in report}
        self.assertEqual(RUNTIME_RELEASE_VERSION, "5.5.2")
        self.assertEqual(by_id["bizhawk_bridge"]["status"], "compatible")
        self.assertEqual(by_id["fastapi_backend"]["observed_version"], "5.5.2")

    def test_disconnected_bridge_is_unavailable_not_compatible(self):
        report = component_version_report(
            bridge_version=BIZHAWK_BRIDGE_VERSION,
            bridge_connected=False,
        )
        bridge = next(item for item in report if item["id"] == "bizhawk_bridge")
        self.assertIsNone(bridge["observed_version"])
        self.assertEqual(bridge["status"], "unavailable")

    def test_bridge_mismatch_is_explicit(self):
        report = component_version_report(bridge_version="1.4.0", bridge_connected=True)
        bridge = next(item for item in report if item["id"] == "bizhawk_bridge")
        self.assertEqual(bridge["status"], "mismatch")
