"""Unit tests for market_relative_underperformance guard (trading-bot deploy)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

FORTRESS_AI = Path(__file__).resolve().parents[1].parent / "fortress-ai"
if not FORTRESS_AI.is_dir():
    FORTRESS_AI = Path("/home/ubuntu/fortress-ai")
if str(FORTRESS_AI) not in sys.path:
    sys.path.insert(0, str(FORTRESS_AI))

from utils.portfolio_session.entry_guard_manager import get_entry_guards  # noqa: E402
from utils.portfolio_session.guards.market_relative import (  # noqa: E402
    MarketRelativeGuard,
    check_market_relative_underperformance,
)
from utils.portfolio_session.guards.market_relative_underperformance import (  # noqa: E402
    MarketRelativeUnderperformanceGuard,
    should_block_entry,
)
from utils.portfolio_session.risk_manager import (  # noqa: E402
    entry_blocked_by_market_relative,
    reset_market_relative_cooldown,
)
from utils.portfolio_session.session_manager import evaluate_entry_blocks, evaluate_entry_guards  # noqa: E402
from utils.portfolio_session.session_monitor import get_session_alpha_vs_spy  # noqa: E402


class TestMarketRelativeGuard(unittest.TestCase):
    def setUp(self) -> None:
        reset_market_relative_cooldown()

    def test_should_block_entry_alpha_minus_0_7(self) -> None:
        self.assertTrue(should_block_entry(-0.7, -0.5))

    def test_should_block_entry_alpha_minus_0_3(self) -> None:
        self.assertFalse(should_block_entry(-0.3, -0.5))

    def test_should_block_entry_alpha_plus_0_2(self) -> None:
        self.assertFalse(should_block_entry(0.2, -0.5))

    def test_check_market_relative_underperformance_si_plan_cases(self) -> None:
        threshold = -0.5
        self.assertTrue(check_market_relative_underperformance(-0.6, threshold))
        self.assertFalse(check_market_relative_underperformance(-0.4, threshold))
        self.assertFalse(check_market_relative_underperformance(0.0, threshold))

    def test_check_market_relative_underperformance_three_arg_returns_reason(self) -> None:
        reason = check_market_relative_underperformance(0.0, 0.64, 0.5)
        self.assertIsNotNone(reason)
        self.assertIn("market_relative_underperformance", str(reason))
        self.assertIn("session_underperforming", str(reason))

        self.assertIsNone(check_market_relative_underperformance(0.0, 0.3, 0.5))
        self.assertIsNone(check_market_relative_underperformance(0.5, 0.2, 0.5))

    def test_market_relative_guard_check_si_plan_cases(self) -> None:
        guard = MarketRelativeGuard(underperformance_threshold=-0.5)
        self.assertTrue(
            guard.check({"alpha_vs_spy_pct": -0.6, "benchmark_ok": True, "component": "portfolio_session"})
        )
        self.assertFalse(
            guard.check({"alpha_vs_spy_pct": -0.4, "benchmark_ok": True, "component": "portfolio_session"})
        )

    def test_should_block_session_context_si_plan(self) -> None:
        guard = MarketRelativeGuard(underperformance_threshold=-0.5)
        self.assertTrue(
            guard.should_block({"alpha_vs_spy_pct": -0.6, "benchmark_ok": True})
        )
        self.assertFalse(
            guard.should_block({"alpha_vs_spy_pct": -0.4, "benchmark_ok": True})
        )
        self.assertFalse(
            guard.should_block({"alpha_vs_spy_pct": 0.2, "benchmark_ok": True})
        )

    def test_blocks_when_alpha_vs_spy_minus_0_005_at_threshold_0_004(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold=0.004)
        blocked, reason = guard.check(-0.5)
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")

    def test_does_not_block_when_alpha_vs_spy_minus_0_003(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold=0.004)
        blocked, reason = guard.check(-0.3)
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_does_not_block_when_disabled(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            threshold_pct=-0.5,
            enabled=False,
        )
        blocked, reason = guard.check(-1.0)
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_evaluate_entry_blocks_si_plan_cases(self) -> None:
        blocked, reason, state = evaluate_entry_blocks(
            {"alpha_vs_spy_pct": -0.6, "benchmark_ok": True, "entry_block_breakdown": {"pattern_disables": 2}}
        )
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")
        self.assertEqual(state["entry_block_breakdown"]["market_relative"], 1)
        self.assertEqual(state["entry_block_breakdown"]["pattern_disables"], 2)

        blocked, reason, state = evaluate_entry_blocks({"alpha_vs_spy_pct": -0.5169, "benchmark_ok": True})
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")
        self.assertEqual(state["entry_block_breakdown"]["market_relative"], 1)

        blocked, reason, _state = evaluate_entry_blocks({"alpha_vs_spy_pct": -0.4, "benchmark_ok": True})
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

        blocked, reason, _state = evaluate_entry_blocks({"alpha_vs_spy_pct": 0.0, "benchmark_ok": True})
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_check_blocks_when_alpha_below_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5)
        blocked, reason = guard.check(-0.8)
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")

    def test_check_allows_when_alpha_above_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5)
        blocked, reason = guard.check(-0.3)
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_check_zero_alpha_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5)
        blocked, reason = guard.check(0.0)
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_check_missing_data_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5)
        blocked, reason = guard.check(session_state={"benchmark_ok": True})
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_blocks_when_alpha_vs_spy_minus_1_2_at_threshold_minus_1_0(self) -> None:
        guard = MarketRelativeGuard(
            underperformance_threshold=1.0,
            config={"component": "portfolio_session"},
        )
        result = guard.evaluate(
            {
                "alpha_vs_spy_pct": -1.2,
                "benchmark_ok": True,
                "component": "portfolio_session",
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_allows_when_alpha_vs_spy_minus_0_5(self) -> None:
        guard = MarketRelativeGuard(
            underperformance_threshold=1.0,
            config={"component": "portfolio_session"},
        )
        result = guard.evaluate(
            {
                "alpha_vs_spy_pct": -0.5,
                "benchmark_ok": True,
                "component": "portfolio_session",
            }
        )
        self.assertFalse(result.blocked)

    def test_skipped_if_component_not_in_session_data(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            underperformance_threshold=1.0,
            config={"component": "portfolio_session"},
        )
        result = guard.evaluate({"alpha_vs_spy_pct": -1.2, "benchmark_ok": True})
        self.assertFalse(result.blocked)
        self.assertEqual(result.detail, "component_skipped")

    def test_session_outperforms_spy_no_block(self) -> None:
        guard = MarketRelativeGuard(underperformance_threshold=0.5)
        result = guard.evaluate({"alpha_vs_spy_pct": 0.5, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_entry_guard_manager_includes_underperformance_guard(self) -> None:
        guards = get_entry_guards()
        self.assertEqual(len(guards), 1)
        self.assertEqual(guards[0].name, "market_relative_underperformance")

    def test_session_manager_increments_entry_block_breakdown(self) -> None:
        result = evaluate_entry_guards(
            {
                "alpha_vs_spy_pct": -1.6,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "entry_block_breakdown": {"denylist": 1},
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_should_block_entry_si_plan_thresholds(self) -> None:
        from utils.portfolio_session.guards.market_relative_underperformance import (
            should_block_entry as _should_block_entry,
        )

        self.assertTrue(_should_block_entry(-0.7, -0.5))
        self.assertFalse(_should_block_entry(-0.3, -0.5))
        self.assertFalse(_should_block_entry(0.2, -0.5))

    def test_entry_blocked_by_market_relative_helper(self) -> None:
        blocked, reason = entry_blocked_by_market_relative(
            {
                "alpha_vs_spy_pct": -1.0,
                "benchmark_ok": True,
                "component": "portfolio_session",
            }
        )
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")

    def test_session_manager_should_block_entry_si_plan(self) -> None:
        from utils.portfolio_session.session_manager import should_block_entry as session_should_block

        self.assertTrue(
            session_should_block(
                {
                    "session_return_pct": 0.0,
                    "benchmark_change_1d_pct": 0.64,
                    "benchmark_ok": True,
                }
            )
        )
        self.assertFalse(
            session_should_block(
                {
                    "session_return_pct": 0.0,
                    "benchmark_change_1d_pct": 0.3,
                    "benchmark_ok": True,
                }
            )
        )

    def test_risk_guards_yaml_config_loading(self) -> None:
        from risk.guards.market_relative_guard import (
            MarketRelativeUnderperformanceGuard,
            load_market_relative_guard_config,
        )

        load_market_relative_guard_config.cache_clear()
        cfg = load_market_relative_guard_config()
        self.assertTrue(cfg.get("enabled"))
        self.assertEqual(cfg.get("threshold_alpha_pct"), -0.5)
        self.assertEqual(cfg.get("check_frequency"), "per_session")

        guard = MarketRelativeUnderperformanceGuard()
        self.assertEqual(guard.threshold_alpha_pct, -0.5)
        self.assertTrue(guard.check(-0.8))
        self.assertFalse(guard.check(-0.3))

    def test_guard_cooldown_blocks_until_alpha_recovers(self) -> None:
        MarketRelativeUnderperformanceGuard.reset_cooldown()
        guard = MarketRelativeUnderperformanceGuard(
            config={"underperformance_threshold_pct": -0.5},
            cooldown_seconds=3600,
        )
        ctx = {"alpha_vs_spy_pct": -0.8, "benchmark_ok": True}
        first = guard.evaluate(ctx)
        self.assertTrue(first.blocked)

        recovered = guard.evaluate({"alpha_vs_spy_pct": -0.2, "benchmark_ok": True})
        self.assertFalse(recovered.blocked)

        MarketRelativeUnderperformanceGuard.reset_cooldown()
        guard.evaluate(ctx)
        still_under = guard.evaluate({"alpha_vs_spy_pct": -0.7, "benchmark_ok": True})
        self.assertTrue(still_under.blocked)
        self.assertIn("cooldown_seconds", still_under.detail or "")

    def test_get_session_alpha_vs_spy_from_monitor(self) -> None:
        from utils.portfolio_session.session_monitor import reset_session_monitor

        reset_session_monitor()
        alpha = get_session_alpha_vs_spy(
            {
                "alpha_vs_spy_pct": -0.64,
                "benchmark_ok": True,
                "session_return_pct": 0.0,
                "benchmark_change_1d_pct": 0.64,
            },
            force=True,
        )
        self.assertEqual(alpha, -0.64)

    def test_config_guards_py_loads_threshold_and_cooldown(self) -> None:
        from config.guards import get_guard_config

        get_guard_config.cache_clear()
        cfg = get_guard_config("market_relative_underperformance")
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["threshold_alpha_pct"], -0.5)
        self.assertEqual(cfg["cooldown_seconds"], 300)

    def test_root_guard_registry_includes_underperformance(self) -> None:
        from guards import GUARD_REGISTRY

        self.assertIn("market_relative_underperformance", GUARD_REGISTRY)


if __name__ == "__main__":
    unittest.main()
