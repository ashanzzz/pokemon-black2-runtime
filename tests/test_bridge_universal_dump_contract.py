"""Static contract checks for Lua features claimed in its hello payload."""

from pathlib import Path
import asyncio
import unittest

from backend.black2.world.runtime_field_resolver import RuntimeFieldLocator


BRIDGE_PATH = Path(__file__).resolve().parents[1] / "bridge" / "bizhawk" / "black2_bridge.lua"


class BridgeUniversalDumpContractTests(unittest.TestCase):
    def test_advertised_universal_dump_has_a_registered_handler(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertIn('local BRIDGE_VERSION = "1.5.1-universal-dump"', source)
        self.assertIn("universal_dump = true", source)
        self.assertIn('elseif op == "memory.dump_universal" then', source)
        self.assertIn("dump_universal_memory(payload, current_frame)", source)
        self.assertIn("[Bridge][memory.dump_universal]", source)

    def test_background_player_sample_never_starts_full_ram_discovery(self) -> None:
        class NoIoReader:
            pass

        result = asyncio.run(RuntimeFieldLocator().sample_player(NoIoReader()))
        self.assertEqual(result["status"], "unresolved")
        self.assertIn("disabled for background sampling", result["reason"])


if __name__ == "__main__":
    unittest.main()
