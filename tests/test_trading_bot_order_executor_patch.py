"""Validate trading-bot order_executor deploy patch."""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

from tests.support.patch_imports import restore_sys_modules, stash_sys_modules

_PATCH_MODULE_KEYS = ("utils", "utils.order_sizer", "utils.order_executor")


def _load_order_executor_module():
    patch_root = Path(__file__).resolve().parent.parent / "deploy" / "trading-bot-patches"
    oe_path = patch_root / "utils" / "order_executor.py"
    stub = types.ModuleType("utils.order_sizer")

    def plan_chunked_exit(shares: int, mark_price: float) -> dict:
        cap = float(os.environ.get("FORTRESS_MAX_ORDER_NOTIONAL_USD", "3000"))
        qty = int(shares)
        px = float(mark_price)
        max_per = max(1, int(cap // px))
        order_qtys: list[int] = []
        remaining = qty
        while remaining > 0:
            q = min(remaining, max_per)
            order_qtys.append(q)
            remaining -= q
        return {
            "order_qtys": order_qtys,
            "chunked_exit": len(order_qtys) > 1,
            "max_notional_usd": cap,
        }

    def create_exit_order(shares: int, mark_price: float) -> dict:
        return plan_chunked_exit(shares, mark_price)

    def submit_chunked_sell_orders(ticker: str, shares: int, mark_price: float, *, submit_one):
        plan = create_exit_order(shares, mark_price)
        submitted = []
        for q in plan["order_qtys"]:
            submitted.append(submit_one(ticker, q))
        return {
            "success": True,
            "chunked_exit": plan.get("chunked_exit"),
            "submitted": submitted,
        }

    stub.plan_chunked_exit = plan_chunked_exit
    stub.create_exit_order = create_exit_order
    stub.submit_chunked_sell_orders = submit_chunked_sell_orders
    sys.modules["utils.order_sizer"] = stub

    spec = importlib.util.spec_from_file_location("utils.order_executor", oe_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.order_executor"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestTradingBotOrderExecutorPatch(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        self._mod_stash = stash_sys_modules(*_PATCH_MODULE_KEYS)

    def tearDown(self):
        restore_sys_modules(self._mod_stash)

    def test_execute_order_chunks_large_sell(self):
        mod = _load_order_executor_module()
        calls: list[int] = []

        def submit_one(_sym: str, qty: int) -> dict:
            calls.append(qty)
            return {"success": True, "order_id": f"o{qty}", "filled_qty": qty, "filled_price": 200.0}

        out = mod.execute_order(
            None,
            side="SELL",
            symbol="IBM",
            qty=447,
            mark_price=200.0,
            submit_one=submit_one,
        )
        self.assertTrue(out.get("success"))
        self.assertTrue(out.get("chunked_exit"))
        self.assertEqual(sum(calls), 447)


if __name__ == "__main__":
    unittest.main()
