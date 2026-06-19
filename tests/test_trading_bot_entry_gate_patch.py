"""Validate trading-bot entry_gate deploy patch."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

from tests.support.patch_imports import restore_sys_modules, stash_sys_modules

_PATCH_MODULE_KEYS = (
    "utils",
    "utils.order_sizer",
    "risk",
    "risk.order_utils",
    "risk.position_manager",
    "risk.entry_gate",
)


def _load_entry_gate_module():
    patch_root = Path(__file__).resolve().parent.parent / "deploy" / "trading-bot-patches"
    pm_path = patch_root / "risk" / "position_manager.py"
    eg_path = patch_root / "risk" / "entry_gate.py"

    stub = types.ModuleType("utils.order_sizer")
    stub.chunk_qtys = lambda total_qty, px, max_notional_usd=None: [int(total_qty)]
    stub.max_order_notional_usd = lambda: 3000.0
    stub.chunk_exit_delay_sec = lambda: 0.0
    sys.modules["utils.order_sizer"] = stub

    risk_pkg = types.ModuleType("risk")
    risk_pkg.__path__ = [str(patch_root / "risk")]
    sys.modules["risk"] = risk_pkg

    order_utils_path = patch_root / "risk" / "order_utils.py"
    spec_ou = importlib.util.spec_from_file_location("risk.order_utils", order_utils_path)
    assert spec_ou and spec_ou.loader
    mod_ou = importlib.util.module_from_spec(spec_ou)
    sys.modules["risk.order_utils"] = mod_ou
    spec_ou.loader.exec_module(mod_ou)

    spec_pm = importlib.util.spec_from_file_location("risk.position_manager", pm_path)
    assert spec_pm and spec_pm.loader
    mod_pm = importlib.util.module_from_spec(spec_pm)
    sys.modules["risk.position_manager"] = mod_pm
    spec_pm.loader.exec_module(mod_pm)

    spec_eg = importlib.util.spec_from_file_location("risk.entry_gate", eg_path)
    assert spec_eg and spec_eg.loader
    mod_eg = importlib.util.module_from_spec(spec_eg)
    sys.modules["risk.entry_gate"] = mod_eg
    spec_eg.loader.exec_module(mod_eg)
    return mod_eg


class TestTradingBotEntryGatePatch(unittest.TestCase):
    def setUp(self):
        self._mod_stash = stash_sys_modules(*_PATCH_MODULE_KEYS)

    def tearDown(self):
        restore_sys_modules(self._mod_stash)

    def test_pre_trade_check_blocks_duplicate_entry(self):
        mod = _load_entry_gate_module()
        gate = mod.pre_trade_check("IBM", [{"sym": "IBM", "qty": 447}])
        self.assertTrue(gate.get("blocked"))
        self.assertEqual(gate.get("reason"), "duplicate_entry_accumulation")


if __name__ == "__main__":
    unittest.main()
