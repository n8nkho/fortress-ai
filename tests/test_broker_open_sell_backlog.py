"""Deploy-patch exit gate tests for broker_open_sell_backlog (Classic trading-bot)."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from tests.support.patch_imports import restore_sys_modules, stash_sys_modules

_PATCH_ROOT = Path(__file__).resolve().parent.parent / "deploy" / "trading-bot-patches"
_MODULE_KEYS = (
    "alpaca_execution",
    "alpaca_execution.config",
    "alpaca_execution.exit_gate",
    "alpaca_execution.order_manager",
    "alpaca_execution.order_lifecycle",
    "alpaca_execution.exit_controller",
    "utils.order_sizer",
)


def _load_exit_controller():
    stub = types.ModuleType("utils.order_sizer")

    def submit_chunked_sell_orders(ticker: str, shares: int, mark_price: float, *, submit_one):
        submitted = [submit_one(ticker, int(shares))]
        ok = all(s.get("success") for s in submitted)
        first = submitted[0] if submitted else {}
        return {
            "success": ok,
            "order_id": first.get("order_id"),
            "filled_qty": first.get("filled_qty"),
            "filled_price": first.get("filled_price"),
            "status": first.get("status"),
            "error": first.get("error"),
        }

    stub.submit_chunked_sell_orders = submit_chunked_sell_orders
    sys.modules["utils.order_sizer"] = stub

    if str(_PATCH_ROOT) not in sys.path:
        sys.path.insert(0, str(_PATCH_ROOT))

    for name in ("alpaca_execution.config", "alpaca_execution.exit_gate", "alpaca_execution.order_manager"):
        mod = importlib.import_module(name)
        sys.modules[name] = mod

    spec = importlib.util.spec_from_file_location(
        "alpaca_execution.exit_controller",
        _PATCH_ROOT / "alpaca_execution" / "exit_controller.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["alpaca_execution.exit_controller"] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skip("WIP trading-bot deploy patches incomplete (order_sizer / lifecycle reexport)")
class TestBrokerOpenSellBacklogPatch(unittest.TestCase):
    def setUp(self) -> None:
        self._mod_stash = stash_sys_modules(*_MODULE_KEYS)
        if str(_PATCH_ROOT) not in sys.path:
            sys.path.insert(0, str(_PATCH_ROOT))
        from alpaca_execution.order_manager import OrderManager

        OrderManager._open_exit_order_pending.clear()

    def tearDown(self) -> None:
        restore_sys_modules(self._mod_stash)

    def test_open_exit_order_pending_blocks_duplicate_sell(self) -> None:
        if str(_PATCH_ROOT) not in sys.path:
            sys.path.insert(0, str(_PATCH_ROOT))
        from alpaca_execution.exit_gate import check_open_exit_order_pending, set_open_exit_order_pending

        set_open_exit_order_pending("SPY", False)
        tc = MagicMock()
        sell = MagicMock(symbol="SPY", side=MagicMock(value="sell"), id="o1")
        tc.get_orders.return_value = [sell]
        gate = check_open_exit_order_pending("SPY", tc)
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["block_reason"], "open_exit_order_pending")

    def test_cancel_stale_phantom_then_allow_exit(self) -> None:
        if str(_PATCH_ROOT) not in sys.path:
            sys.path.insert(0, str(_PATCH_ROOT))
        from alpaca_execution.order_manager import OrderManager

        exit_controller = _load_exit_controller()
        submit_exit_order = exit_controller.submit_exit_order

        tc = MagicMock()
        tc.get_all_positions.return_value = []
        phantom = MagicMock(
            symbol="QQQ",
            side=MagicMock(value="sell"),
            id="p1",
            submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        tc.get_orders.side_effect = [[phantom], []]

        om = OrderManager(tc)
        out = om.cancel_stale_open_sell_orders("QQQ")
        self.assertEqual(out["cancelled_count"], 1)

        calls: list[tuple[str, int]] = []

        def _submit_one(sym: str, chunk_qty: int) -> dict:
            calls.append((sym, chunk_qty))
            return {
                "success": True,
                "order_id": "new1",
                "filled_qty": chunk_qty,
                "filled_price": 400.0,
                "status": "filled",
                "error": None,
            }

        tc.get_orders.side_effect = [[], []]
        result = submit_exit_order(
            trading_client=tc,
            symbol="QQQ",
            qty=3,
            mark_price=400.0,
            submit_one=_submit_one,
        )
        self.assertTrue(result["success"])
        self.assertEqual(calls, [("QQQ", 3)])

    def test_cancel_stale_open_sell_orders_all_symbols(self) -> None:
        from alpaca_execution.order_manager import OrderManager

        tc = MagicMock()
        tc.get_all_positions.return_value = []
        phantom = MagicMock(
            symbol="AIQ",
            side=MagicMock(value="sell"),
            id="p2",
            submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        tc.get_orders.return_value = [phantom]

        om = OrderManager(tc)
        out = om.cancel_stale_open_sell_orders()
        self.assertEqual(out["cancelled_count"], 1)
        tc.cancel_order_by_id.assert_called_once_with("p2")

    def test_order_lifecycle_clears_pending_on_fill(self) -> None:
        if str(_PATCH_ROOT) not in sys.path:
            sys.path.insert(0, str(_PATCH_ROOT))
        from alpaca_execution.order_lifecycle import on_order_update
        from alpaca_execution.order_manager import OrderManager

        OrderManager.set_open_exit_order_pending("PLTR", True)
        order = MagicMock(
            symbol="PLTR",
            side=MagicMock(value="sell"),
            id="f1",
            status=MagicMock(value="filled"),
        )
        self.assertTrue(on_order_update(order))
        self.assertFalse(OrderManager.is_open_exit_order_pending("PLTR"))

    def test_execution_engine_clear_exit_pending(self) -> None:
        if str(_PATCH_ROOT) not in sys.path:
            sys.path.insert(0, str(_PATCH_ROOT))
        from alpaca_execution.execution_engine import clear_exit_pending, on_exit_order_terminal
        from alpaca_execution.order_manager import OrderManager

        OrderManager.set_open_exit_order_pending("NVDA", True)
        order = MagicMock(
            symbol="NVDA",
            side=MagicMock(value="sell"),
            id="x1",
            status=MagicMock(value="canceled"),
        )
        self.assertTrue(on_exit_order_terminal(order))
        self.assertFalse(OrderManager.is_open_exit_order_pending("NVDA"))
        OrderManager.set_open_exit_order_pending("NVDA", True)
        clear_exit_pending("NVDA")
        self.assertFalse(OrderManager.is_open_exit_order_pending("NVDA"))


    def test_order_status_handler_reexports_lifecycle(self) -> None:
        if str(_PATCH_ROOT) not in sys.path:
            sys.path.insert(0, str(_PATCH_ROOT))
        from alpaca_execution.order_status_handler import on_order_update as handler_update
        from alpaca_execution.order_lifecycle import on_order_update

        self.assertIs(handler_update, on_order_update)

    def test_place_exit_order_in_execution_engine(self) -> None:
        if str(_PATCH_ROOT) not in sys.path:
            sys.path.insert(0, str(_PATCH_ROOT))
        from alpaca_execution.execution_engine import place_exit_order
        from alpaca_execution.order_manager import OrderManager

        tc = MagicMock()
        tc.get_all_positions.return_value = [MagicMock(symbol="SPY", qty="10")]
        tc.get_orders.return_value = []
        calls: list[tuple[str, int]] = []

        def _submit_one(sym: str, chunk_qty: int) -> dict:
            calls.append((sym, chunk_qty))
            return {
                "success": True,
                "order_id": "n1",
                "filled_qty": chunk_qty,
                "filled_price": 100.0,
                "status": "filled",
            }

        out = place_exit_order(
            trading_client=tc,
            symbol="SPY",
            qty=2,
            mark_price=100.0,
            submit_one=_submit_one,
        )
        self.assertTrue(out["success"])
        self.assertEqual(calls, [("SPY", 2)])
        self.assertFalse(OrderManager.is_open_exit_order_pending("SPY"))

    def test_stale_order_age_seconds_from_alpaca_settings(self) -> None:
        if str(_PATCH_ROOT) not in sys.path:
            sys.path.insert(0, str(_PATCH_ROOT))
        from config.alpaca_settings import STALE_ORDER_AGE_SECONDS

        self.assertEqual(STALE_ORDER_AGE_SECONDS, 300)


class TestFortressStaleSellCleanup(unittest.TestCase):
    def test_on_exit_submit_result_clears_on_terminal(self) -> None:
        from utils.alpaca_execution import (
            is_open_exit_order_pending,
            on_exit_submit_result,
            set_open_exit_order_pending,
        )

        set_open_exit_order_pending("SPY", True)
        self.assertTrue(on_exit_submit_result({"success": True, "status": "filled", "order_id": "o1"}, symbol="SPY"))
        self.assertFalse(is_open_exit_order_pending("SPY"))

    def test_on_exit_submit_result_retains_pending_while_open(self) -> None:
        from utils.alpaca_execution import (
            is_open_exit_order_pending,
            on_exit_submit_result,
            set_open_exit_order_pending,
        )

        set_open_exit_order_pending("QQQ", True)
        self.assertFalse(on_exit_submit_result({"success": True, "status": "accepted", "order_id": "o2"}, symbol="QQQ"))
        self.assertTrue(is_open_exit_order_pending("QQQ"))

    def test_maybe_cancel_stale_open_sell_orders_throttled(self) -> None:
        from utils import alpaca_execution as ae

        ae._LAST_STALE_SELL_CLEANUP_TS = 0.0
        with unittest.mock.patch.object(ae, "cancel_all_stale_open_sell_orders") as mock_cancel:
            mock_cancel.return_value = {"ok": True, "cancelled_count": 0}
            first = ae.maybe_cancel_stale_open_sell_orders(force=True)
            second = ae.maybe_cancel_stale_open_sell_orders()
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            mock_cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
