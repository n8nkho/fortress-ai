"""Unit tests for MarketRelativeGate."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from utils.portfolio_session.gates.market_relative_gate import (
    MarketRelativeGate,
    compute_lookback_alpha,
)
from utils.portfolio_session.risk_manager import RiskManager, load_market_relative_gate_config, reset_market_relative_cooldown


class TestMarketRelativeGate(unittest.TestCase):
    def test_allows_when_alpha_above_threshold(self) -> None:
        gate = MarketRelativeGate(max_underperformance_pct=-0.5, lookback_minutes=60)
        result = gate.evaluate({"alpha_vs_spy_pct": -0.2, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_blocks_when_alpha_below_threshold(self) -> None:
        gate = MarketRelativeGate(max_underperformance_pct=-0.5, lookback_minutes=60)
        result = gate.evaluate({"alpha_vs_spy_pct": -0.8, "benchmark_ok": True})
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_alpha_vs_spy_alias_blocks_at_minus_one(self) -> None:
        gate = MarketRelativeGate(max_underperformance_pct=-0.5, lookback_minutes=60)
        result = gate.evaluate({"alpha_vs_spy": -1.0, "benchmark_ok": True})
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_alpha_vs_spy_alias_passes_at_minus_three_tenths(self) -> None:
        gate = MarketRelativeGate(max_underperformance_pct=-0.5, lookback_minutes=60)
        result = gate.evaluate({"alpha_vs_spy": -0.3, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_disabled_gate_never_blocks(self) -> None:
        gate = MarketRelativeGate(enabled=False, max_underperformance_pct=-0.5)
        result = gate.evaluate({"alpha_vs_spy_pct": -2.0, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_zero_window_without_alpha_does_not_block(self) -> None:
        gate = MarketRelativeGate(max_underperformance_pct=-0.5, lookback_minutes=0, window_seconds=0)
        result = gate.evaluate({"benchmark_ok": True})
        self.assertFalse(result.blocked)
        self.assertEqual(result.detail, "missing_alpha_data")

    def test_integration_spy_up_portfolio_down_blocks(self) -> None:
        """SPY +0.5%, portfolio -0.2% → alpha -0.7% blocks at default -0.3% threshold."""
        gate = MarketRelativeGate(max_underperformance_pct=-0.3, window_seconds=300)
        result = gate.evaluate(
            {
                "benchmark_ok": True,
                "session_return_pct": -0.2,
                "benchmark_change_1d_pct": 0.5,
                "alpha_vs_spy_pct": -0.7,
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_missing_data_does_not_block(self) -> None:
        gate = MarketRelativeGate(max_underperformance_pct=-0.5, lookback_minutes=60)
        result = gate.evaluate({"benchmark_ok": False})
        self.assertFalse(result.blocked)

    def test_lookback_snapshots_compute_alpha(self) -> None:
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(minutes=30)
        state = {
            "benchmark_ok": True,
            "alpha_snapshots": [
                {
                    "ts": earlier.isoformat(),
                    "portfolio_return_pct": 0.1,
                    "spy_return_pct": 0.4,
                },
                {
                    "ts": now.isoformat(),
                    "portfolio_return_pct": 0.2,
                    "spy_return_pct": 0.7,
                },
            ],
        }
        alpha = compute_lookback_alpha(state, 60)
        self.assertEqual(alpha, -0.2)

    def test_risk_manager_evaluates_registry_gate(self) -> None:
        reset_market_relative_cooldown()
        gate = MarketRelativeGate(max_underperformance_pct=-0.5, lookback_minutes=60)
        manager = RiskManager(gates=[gate], cooldown_seconds=0)
        blocked = manager.evaluate_pre_trade_gates({"alpha_vs_spy_pct": -1.0, "benchmark_ok": True})
        self.assertTrue(blocked.blocked)
        self.assertEqual(blocked.reason, "market_relative_underperformance")

    def test_cooldown_holds_block_after_alpha_recovers(self) -> None:
        reset_market_relative_cooldown()
        gate = MarketRelativeGate(max_underperformance_pct=-0.5, lookback_minutes=60)
        manager = RiskManager(gates=[gate], cooldown_seconds=3600)
        first = manager.evaluate_pre_trade_gates({"alpha_vs_spy": -1.0, "benchmark_ok": True})
        self.assertTrue(first.blocked)
        second = manager.evaluate_pre_trade_gates({"alpha_vs_spy": 0.5, "benchmark_ok": True})
        self.assertTrue(second.blocked)
        self.assertIn("cooldown_seconds", second.detail)

    def test_config_defaults(self) -> None:
        cfg = load_market_relative_gate_config()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["max_underperformance_pct"], -0.5)
        self.assertEqual(cfg["window_seconds"], 300)
        self.assertEqual(cfg["cooldown_seconds"], 3600)


if __name__ == "__main__":
    unittest.main()
