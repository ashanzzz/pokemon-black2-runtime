import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run_runtime import load_local_config


class TestRuntimeLocalConfig(unittest.TestCase):
    def test_local_rom_path_is_loaded_before_backend_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime.local.json"
            path.write_text(json.dumps({"rom_path": "D:/games/black2.nds", "http_port": 9876}), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BLACK2_ROM_PATH", None)
                os.environ.pop("BLACK2_HTTP_PORT", None)
                result = load_local_config(path)
                self.assertEqual(os.environ["BLACK2_ROM_PATH"], "D:/games/black2.nds")
                self.assertEqual(os.environ["BLACK2_HTTP_PORT"], "9876")
                self.assertEqual(result["http_port"], 9876)
