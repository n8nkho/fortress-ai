"""Tests for trading-bot execution/order_splitting deploy patch."""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

from tests.support.patch_imports import restore_sys_modules, stash_sys_modules

_PATCH_MODULE_KEYS = ("utils", "utils.order_sizer")


def _load_patch_module():
    patch_root = Path(__file__).resolve().parent.parent / "deploy" / "trading-bot-patches"
    path = patch_root / "execution" / "order_splitting.py"
    utils_stub = types.ModuleType("utils")
    order_sizer = types.ModuleType("utils.order_sizer")

    def chunk_qtys(total_qty, px, max_notional_usd=None):
        cap = max_notional_usd or 3000.0
        max_per = max(1, int(cap // float(px)))
        chunks, remaining = [], int(total_qty)
        while remaining > 0:
            q = min(remaining, max_per)
            chunks.append(q)
            remaining -= q
        return chunks

    order_sizer.chunk_qtys = chunk_qtys
    order_sizer.max_order_notional_usd = lambda: 3000.0
    sys.modules["utils"] = utils_stub
    sys.modules["utils.order_sizer"] = order_sizer

    spec = importlib.util.spec_from_file_location("_tb_order_splitting", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTradingBotOrderSplittingPatch(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        self._mod_stash = stash_sys_modules(*_PATCH_MODULE_KEYS)

    def tearDown(self):
        restore_sys_modules(self._mod_stash)

    def test_chunk_exit_order_splits_oversized_exit(self):
        mod = _load_patch_module()
        pairs = mod.chunk_exit_order("IBM", 447, 3000.0, px=200.0)
        self.assertGreater(len(pairs), 1)
        self.assertEqual(sum(q for q, _ in pairs), 447)
        self.assertTrue(all(q * px <= 3000.0 for q, px in pairs))

    def test_chunk_exit_order_single_chunk_under_cap(self):
        mod = _load_patch_module()
        pairs = mod.chunk_exit_order("IBM", 5, 3000.0, px=200.0)
        self.assertEqual(pairs, [(5, 200.0)])


if __name__ == "__main__":
    unittest.main()
