import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from backend.black2.state.engine import SemanticGameState
from backend.black2.state.universal_snapshot_manager import DUMP_DOMAINS, UniversalSnapshotManager


class _StateEngine:
    async def sample_once(self):
        return SemanticGameState(timestamp=1.0, frame=1234)


class _Transport:
    async def request(self, operation, payload=None):
        if operation == "memory.domains":
            return {
                "Main RAM": {"size": 0x400000, "readable": True},
                "Instruction TCM": {"size": 0x8000, "readable": True},
                "Data TCM": {"size": 0x4000, "readable": True},
                "Shared WRAM": {"size": 0x8000, "readable": True},
                "ARM7 WRAM": {"size": 0x10000, "readable": True},
                "SRAM": {"size": 0x80000, "readable": True},
                "ARM9 BIOS": {"size": 0x1000, "readable": True},
                "ROM": {"size": 0x20000000, "readable": True},
                "ARM9 System Bus": {"size": 0, "readable": True},
            }
        assert operation == "memory.dump_universal"
        output = Path(payload["dump_dir"])
        domains = {}
        for spec in payload["domains"]:
            data = bytearray(spec["size"])
            # Two agreeing physical RAM mirrors prove only a candidate map ID.
            if spec["name"] == "Main RAM":
                for offset in (0x1434A6, 0x143668):
                    data[offset:offset + 2] = (42).to_bytes(2, "little")
            (output / spec["file"]).write_bytes(data)
            domains[spec["name"]] = {"size": spec["size"], "expected": spec["size"], "success": True}
        Path(payload["png_path"]).write_bytes(b"png")
        return {
            "frame": 1240,
            "written_bytes": sum(size for _name, _file, size in DUMP_DOMAINS),
            "domains": domains,
            "screenshot_saved": True,
            "registers": {"r0": 1},
        }


class UniversalSnapshotManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_universal_export_requires_each_memory_domain_and_emits_runtime_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = UniversalSnapshotManager(Path(temp_dir))
            result = await manager.create_snapshot(_Transport(), _StateEngine(), label="evidence")

            self.assertTrue(result["complete"])
            self.assertTrue(result["bundle"]["verification"]["ok"])
            self.assertEqual(result["bridge_written_bytes"], 0x400000)  # not the multi-domain total
            self.assertTrue(all(record["complete"] for record in result["memory_domains"]))
            folder = Path(result["folder"])
            exported_files = {record["file"] for record in result["memory_domains"]}
            self.assertIn("arm9_bios.bin", exported_files)
            self.assertNotIn("rom.bin", exported_files)
            self.assertTrue(exported_files <= {path.name for path in folder.iterdir()})
            with zipfile.ZipFile(folder / result["bundle"]["file_name"]) as bundle:
                self.assertIn("screen.png", bundle.namelist())
                self.assertTrue(exported_files <= set(bundle.namelist()))
                self.assertIn("memory_domain_inventory.json", bundle.namelist())
            world = json.loads((folder / "runtime_world.json").read_text(encoding="utf-8"))
            self.assertEqual(world["map"]["map_section_id"]["value"], 42)
            self.assertEqual(world["map"]["map_section_id"]["confidence"], "candidate")
            self.assertEqual(world["actors"]["runtime_actors"], [])
            self.assertEqual(world["actors"]["npc_names"]["confidence"], "unresolved")
            self.assertTrue(manager.list_snapshots()[0]["complete"])
            bundle_path, verification = manager.verified_bundle_path(result["snapshot_id"])
            self.assertEqual(bundle_path, folder / result["bundle"]["file_name"])
            self.assertTrue(verification["ok"])
            cleared = manager.clear_snapshots()
            self.assertEqual(cleared["deleted_count"], 1)
            self.assertFalse(folder.exists())
