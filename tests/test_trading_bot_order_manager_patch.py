"""Validate trading-bot order_manager deploy patch (no trading-bot write required)."""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

from tests.support.patch_imports import restore_sys_modules, stash_sys_modules

_PATCH_MODULE_KEYS = ("utils", "utils.order_sizer", "execution.order_manager")


def _load_order_manager_module():
    patch_root = Path(__file__).resolve().parent.parent / "deploy" / "trading-bot-patches"
    om_path = patch_root / "execution" / "order_manager.py"
    stub = types.ModuleType("utils.order_sizer")
    stub.max_order_notional_usd = lambda: float(os.environ.get("FORTRESS_MAX_ORDER_NOTIONAL_USD", "3000"))
    stub.chunk_exit_delay_sec = lambda: 0.0

    def chunk_qtys(total_qty: int, px: float, max_notional_usd: float | None = None) -> list[int]:
        cap = max_notional_usd if max_notional_usd is not None else stub.max_order_notional_usd()
        if total_qty <= 0 or px <= 0:
            return [total_qty] if total_qty > 0 else []
        max_per = max(1, int(cap // float(px)))
        chunks: list[int] = []
        remaining = int(total_qty)
        while remaining > 0:
            q = min(remaining, max_per)
            chunks.append(q)
            remaining -= q
        return chunks

    def chunk_exit_order(symbol: str, total_qty: int, max_notional: float | None = None, *, px: float):
        cap = float(max_notional) if max_notional is not None else stub.max_order_notional_usd()
        price = float(px or 0)
        qtys = chunk_qtys(int(total_qty), price, max_notional_usd=cap)
        return [(q, price) for q in qtys]

    stub.chunk_qtys = chunk_qtys
    stub.chunk_exit_order = chunk_exit_order
    sys.modules["utils.order_sizer"] = stub

    spec = importlib.util.spec_from_file_location("execution.order_manager", om_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["execution.order_manager"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestTradingBotOrderManagerPatch(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        self._mod_stash = stash_sys_modules(*_PATCH_MODULE_KEYS)

    def tearDown(self):
        restore_sys_modules(self._mod_stash)

    def test_flatten_legacy_positions_plans_chunked_trim(self):
        mod = _load_order_manager_module()
        positions = [{"sym": "IBM", "qty": 447, "mkt_value": 89400.0}]
        summary = mod.OrderManager(None).flatten_legacy_positions(positions, dry_run=True)
        self.assertEqual(len(summary.get("flattened") or []), 1)
        rec = summary["flattened"][0]
        self.assertEqual(rec["symbol"], "IBM")
        self.assertTrue(rec.get("chunked_exit"))
        self.assertGreater(len(rec.get("order_qtys") or []), 1)

    def test_validate_order_notional_rejects_oversized(self):
        mod = _load_order_manager_module()
        gate = mod.validate_order_notional(447, 200.0, side="SELL", symbol="IBM")
        self.assertFalse(gate.get("allowed"))
        self.assertEqual(gate.get("block_reason"), "estimated_notional_exceeds_cap")
        self.assertEqual(gate.get("suggest"), "chunked_exit")


if __name__ == "__main__":
    unittest.main()
