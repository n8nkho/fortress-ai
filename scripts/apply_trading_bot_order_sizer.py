#!/usr/bin/env python3
"""Deploy chunked exit order sizing to trading-bot (SI fix duplicate_entry_accumulation)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TRADING_BOT = Path("/home/ubuntu/trading-bot")

ORDER_SIZER = '''"""Exit order sizing — chunk large sells under FORTRESS_MAX_ORDER_NOTIONAL_USD."""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

CHUNK_DELAY_MIN_SEC = 0.1
CHUNK_DELAY_MAX_SEC = 0.5


def max_order_notional_usd() -> float:
    try:
        return float(os.environ.get("FORTRESS_MAX_ORDER_NOTIONAL_USD", "25000"))
    except ValueError:
        return 25000.0


def chunk_qtys(total_qty: int, px: float, max_notional_usd: float | None = None) -> list[int]:
    """Split total_qty into order chunks that each fit under max_notional_usd."""
    if total_qty <= 0:
        return []
    cap = max_notional_usd if max_notional_usd is not None else max_order_notional_usd()
    if px <= 0:
        return [total_qty]
    max_per = max(1, int(cap // float(px)))
    chunks: list[int] = []
    remaining = int(total_qty)
    while remaining > 0:
        q = min(remaining, max_per)
        chunks.append(q)
        remaining -= q
    return chunks


def chunk_exit_delay_sec() -> float:
    """Random inter-order delay for chunked exits."""
    return random.uniform(CHUNK_DELAY_MIN_SEC, CHUNK_DELAY_MAX_SEC)


def plan_chunked_exit(shares: int, mark_price: float) -> dict[str, Any]:
    """
    Plan exit order quantities under the notional cap.

    Returns dict with order_qtys, chunked_exit flag, and cap metadata.
    """
    try:
        qty = int(abs(float(shares or 0)))
    except (TypeError, ValueError):
        qty = 0
    try:
        px = float(mark_price or 0)
    except (TypeError, ValueError):
        px = 0.0

    cap = max_order_notional_usd()
    result: dict[str, Any] = {
        "order_qtys": [],
        "chunked_exit": False,
        "max_notional_usd": cap,
        "total_qty": qty,
        "mark_price": px,
    }
    if qty <= 0:
        result["block_reason"] = "invalid_qty"
        return result

    order_qtys = chunk_qtys(qty, px, cap)
    if not order_qtys:
        result["block_reason"] = "invalid_chunk_qty"
        return result

    if len(order_qtys) > 1:
        result["chunked_exit"] = True
        result["chunk_count"] = len(order_qtys)
        total_notional = qty * px if px > 0 else 0.0
        logger.info(
            "chunked_exit:plan notional=%.2f cap=%.2f chunks=%d",
            total_notional,
            cap,
            len(order_qtys),
        )

    result["order_qtys"] = order_qtys
    return result


def submit_chunked_sell_orders(
    client: Any,
    ticker: str,
    plan: dict[str, Any],
    *,
    mark_price: float,
    gate_fn: Callable[..., dict[str, Any]],
    format_gate_block: Callable[[dict[str, Any]], str],
    append_trust_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """
    Submit sell orders from a chunked exit plan with pre-trade gate checks and random delays.
    """
    sym = str(ticker or "").upper()
    order_qtys = plan.get("order_qtys") or []
    if not order_qtys:
        return {
            "success": False,
            "order_id": None,
            "filled_qty": None,
            "filled_price": None,
            "error": plan.get("block_reason") or "invalid_chunk_qty",
        }

    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    px = float(mark_price or plan.get("mark_price") or 0)
    submitted: list[dict[str, Any]] = []
    last_result: dict[str, Any] | None = None

    for i, chunk_qty in enumerate(order_qtys):
        if i > 0 and plan.get("chunked_exit"):
            delay = chunk_exit_delay_sec()
            logger.info(
                "chunked_exit:%s delay=%.3fs before chunk %d/%d",
                sym,
                delay,
                i + 1,
                len(order_qtys),
            )
            time.sleep(delay)

        try:
            est = float(chunk_qty) * float(px or 0)
        except (TypeError, ValueError):
            est = 0.0

        gate = gate_fn(
            side="SELL",
            symbol=sym,
            qty=float(chunk_qty),
            estimated_notional_usd=est if est > 0 else None,
        )
        if not gate.get("allowed"):
            logger.warning(f"{sym}: pre_trade_gate blocked: {gate.get('reasons')}")
            if append_trust_event is not None:
                append_trust_event(
                    "pre_trade_gate_blocked",
                    {
                        "ticker": sym,
                        "pattern": "stock_sell",
                        "gate": gate,
                        "chunked_exit": bool(plan.get("chunked_exit")),
                    },
                )
            if submitted:
                return {
                    "success": True,
                    "partial": True,
                    "chunked_exit": bool(plan.get("chunked_exit")),
                    "order_id": submitted[-1].get("order_id"),
                    "filled_qty": sum(int(o.get("filled_qty") or 0) for o in submitted),
                    "filled_price": submitted[-1].get("filled_price"),
                    "status": submitted[-1].get("status"),
                    "orders": submitted,
                    "error": format_gate_block(gate),
                }
            return {
                "success": False,
                "order_id": None,
                "filled_qty": None,
                "filled_price": None,
                "error": format_gate_block(gate),
            }

        try:
            logger.info(
                "%s: Submitting SELL order for %s shares (chunk %d/%d)",
                sym,
                chunk_qty,
                i + 1,
                len(order_qtys),
            )
            order_data = MarketOrderRequest(
                symbol=sym,
                qty=chunk_qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = client.submit_order(order_data)
            chunk_result = {
                "success": True,
                "order_id": str(order.id),
                "filled_qty": int(order.filled_qty) if order.filled_qty else None,
                "filled_price": float(order.filled_avg_price) if order.filled_avg_price else None,
                "status": str(order.status),
                "error": None,
                "qty": chunk_qty,
            }
            submitted.append(chunk_result)
            last_result = chunk_result
            logger.info("%s: Order submitted - ID: %s, Status: %s", sym, order.id, order.status)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            logger.error(f"{sym}: Error executing sell order: {err}")
            if submitted:
                return {
                    "success": True,
                    "partial": True,
                    "chunked_exit": bool(plan.get("chunked_exit")),
                    "order_id": submitted[-1].get("order_id"),
                    "filled_qty": sum(int(o.get("filled_qty") or 0) for o in submitted),
                    "filled_price": submitted[-1].get("filled_price"),
                    "status": submitted[-1].get("status"),
                    "orders": submitted,
                    "error": err,
                }
            return {
                "success": False,
                "order_id": None,
                "filled_qty": None,
                "filled_price": None,
                "error": err,
            }

    if not last_result:
        return {
            "success": False,
            "order_id": None,
            "filled_qty": None,
            "filled_price": None,
            "error": "no_orders_submitted",
        }

    out = dict(last_result)
    out["success"] = True
    if plan.get("chunked_exit"):
        out["chunked_exit"] = True
        out["chunk_count"] = len(order_qtys)
        out["orders"] = submitted
        out["filled_qty"] = sum(
            int(o.get("filled_qty") or 0) for o in submitted if o.get("filled_qty")
        )
    return out
'''

TEST_ORDER_SIZER = '''"""Tests for chunked exit order sizing."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestOrderSizer(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"

    def tearDown(self):
        os.environ.pop("FORTRESS_MAX_ORDER_NOTIONAL_USD", None)

    def test_max_order_notional_default(self):
        os.environ.pop("FORTRESS_MAX_ORDER_NOTIONAL_USD", None)
        from utils.order_sizer import max_order_notional_usd

        self.assertEqual(max_order_notional_usd(), 25000.0)

    def test_chunk_qtys_splits_under_cap(self):
        from utils.order_sizer import chunk_qtys

        chunks = chunk_qtys(447, 200.0, 3000.0)
        self.assertEqual(sum(chunks), 447)
        self.assertTrue(all(q * 200.0 <= 3000.0 for q in chunks))
        self.assertGreater(len(chunks), 1)

    def test_plan_chunks_large_exit(self):
        from utils.order_sizer import plan_chunked_exit

        plan = plan_chunked_exit(447, 200.0)
        self.assertTrue(plan.get("chunked_exit"))
        self.assertGreater(len(plan.get("order_qtys") or []), 1)
        self.assertEqual(sum(plan["order_qtys"]), 447)

    def test_plan_no_chunk_when_under_cap(self):
        from utils.order_sizer import plan_chunked_exit

        plan = plan_chunked_exit(5, 200.0)
        self.assertEqual(plan.get("order_qtys"), [5])
        self.assertFalse(plan.get("chunked_exit"))

    def test_submit_chunked_sell_with_delays(self):
        from utils.order_sizer import plan_chunked_exit, submit_chunked_sell_orders

        plan = plan_chunked_exit(447, 200.0)
        client = MagicMock()
        order = MagicMock()
        order.id = "oid-1"
        order.status = "accepted"
        order.filled_qty = None
        order.filled_avg_price = None
        client.submit_order.return_value = order

        gate_fn = MagicMock(return_value={"allowed": True})
        with patch("utils.order_sizer.time.sleep") as mock_sleep:
            result = submit_chunked_sell_orders(
                client,
                "IBM",
                plan,
                mark_price=200.0,
                gate_fn=gate_fn,
                format_gate_block=lambda g: str(g),
            )

        self.assertTrue(result.get("success"))
        self.assertTrue(result.get("chunked_exit"))
        self.assertGreater(client.submit_order.call_count, 1)
        self.assertGreater(mock_sleep.call_count, 0)
        self.assertEqual(gate_fn.call_count, client.submit_order.call_count)

    def test_submit_blocked_on_first_chunk(self):
        from utils.order_sizer import plan_chunked_exit, submit_chunked_sell_orders

        plan = plan_chunked_exit(5, 200.0)
        client = MagicMock()
        gate_fn = MagicMock(return_value={"allowed": False, "reasons": ["test_block"]})

        result = submit_chunked_sell_orders(
            client,
            "IBM",
            plan,
            mark_price=200.0,
            gate_fn=gate_fn,
            format_gate_block=lambda g: "blocked",
        )

        self.assertFalse(result.get("success"))
        self.assertEqual(result.get("error"), "blocked")
        client.submit_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
'''

ORCHESTRATOR_OLD = """    try:
        est = float(shares) * float(px or 0)
    except Exception:
        est = 0.0

    gate = evaluate_pre_trade_submission(
        side="SELL",
        symbol=ticker,
        qty=float(shares),
        estimated_notional_usd=est if est > 0 else None,
    )
    if not gate["allowed"]:
        logger.warning(f"{ticker}: pre_trade_gate blocked: {gate.get('reasons')}")
        append_trust_event(
            "pre_trade_gate_blocked",
            {"ticker": ticker, "pattern": "stock_sell", "gate": gate},
        )
        return {
            "success": False,
            "order_id": None,
            "filled_qty": None,
            "filled_price": None,
            "error": format_gate_block_message(gate),
        }
    
    try:
        logger.info(f"{ticker}: Submitting SELL order for {shares} shares")
        
        # Create market order request
        order_data = MarketOrderRequest(
            symbol=ticker,
            qty=shares,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )
        
        # Submit order
        order = alpaca_client.submit_order(order_data)
        
        logger.info(f"{ticker}: Order submitted - ID: {order.id}, Status: {order.status}")
        
        # Return order details
        return {
            'success': True,
            'order_id': str(order.id),
            'filled_qty': int(order.filled_qty) if order.filled_qty else None,
            'filled_price': float(order.filled_avg_price) if order.filled_avg_price else None,
            'status': str(order.status),
            'error': None
        }
        
    except Exception as e:"""

ORCHESTRATOR_NEW = """    from utils.order_sizer import plan_chunked_exit, submit_chunked_sell_orders

    plan = plan_chunked_exit(shares, px)
    if plan.get("block_reason"):
        return {
            "success": False,
            "order_id": None,
            "filled_qty": None,
            "filled_price": None,
            "error": plan.get("block_reason"),
        }
    if plan.get("chunked_exit"):
        logger.info(
            "%s: chunked_exit notional=%.2f cap=%.2f chunks=%d",
            ticker,
            float(shares) * float(px or 0),
            plan.get("max_notional_usd"),
            len(plan.get("order_qtys") or []),
        )

    try:
        return submit_chunked_sell_orders(
            alpaca_client,
            ticker,
            plan,
            mark_price=px,
            gate_fn=evaluate_pre_trade_submission,
            format_gate_block=format_gate_block_message,
            append_trust_event=append_trust_event,
        )
    except Exception as e:"""


def main() -> int:
    if not TRADING_BOT.is_dir():
        print(f"trading-bot not found at {TRADING_BOT}", file=sys.stderr)
        return 1

    (TRADING_BOT / "utils").mkdir(parents=True, exist_ok=True)
    (TRADING_BOT / "tests").mkdir(parents=True, exist_ok=True)

    order_sizer_path = TRADING_BOT / "utils" / "order_sizer.py"
    order_sizer_path.write_text(ORDER_SIZER, encoding="utf-8")
    print(f"wrote {order_sizer_path}")

    test_path = TRADING_BOT / "tests" / "test_order_sizer.py"
    test_path.write_text(TEST_ORDER_SIZER, encoding="utf-8")
    print(f"wrote {test_path}")

    orch_path = TRADING_BOT / "orchestrator.py"
    orch_text = orch_path.read_text(encoding="utf-8")
    if ORCHESTRATOR_OLD not in orch_text:
        if "plan_chunked_exit" in orch_text:
            print(f"orchestrator.py already patched ({orch_path})")
        else:
            print("orchestrator.py: expected block not found; manual patch required", file=sys.stderr)
            return 1
    else:
        orch_path.write_text(orch_text.replace(ORCHESTRATOR_OLD, ORCHESTRATOR_NEW), encoding="utf-8")
        print(f"patched {orch_path}")

    result = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_order_sizer", "-v"],
        cwd=TRADING_BOT,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
