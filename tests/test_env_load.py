"""Fortress .env merge with empty shell exports."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestEnvLoad(unittest.TestCase):
    def test_dotenv_overrides_empty_alpaca_exports(self):
        from utils import env_load

        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        (td / ".env").write_text(
            "ALPACA_API_KEY=keyfromenv\nALPACA_SECRET_KEY=secretfromenv\n",
            encoding="utf-8",
        )
        os.environ["ALPACA_API_KEY"] = ""
        os.environ["ALPACA_SECRET_KEY"] = ""
        try:
            env_load.load_fortress_dotenv(td)
            self.assertEqual(os.environ.get("ALPACA_API_KEY"), "keyfromenv")
            self.assertEqual(os.environ.get("ALPACA_SECRET_KEY"), "secretfromenv")
        finally:
            os.environ.pop("ALPACA_API_KEY", None)
            os.environ.pop("ALPACA_SECRET_KEY", None)


if __name__ == "__main__":
    unittest.main()
