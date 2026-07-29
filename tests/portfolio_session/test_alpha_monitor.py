"""Unit tests for portfolio_session alpha_monitor — negative_alpha_active_session."""
from __future__ import annotations

import unittest

from utils.portfolio_session.alpha_monitor import check_session_alpha
from utils.portfolio_session.session_manager import (
    finalize_session_after_exit,
    is_symbol_disabled_for_negative_alpha,
    reset_negative_alpha_disabled_symbols,
)


class TestCheckSessionAlpha(unittest.TestCase):
    def setUp(self) -> None:
        reset_negative_alpha_disabled_symbols()

    def tearDown(self) -> None:
        reset_negative_alpha_disabled_symbols()

    def test_flags_negative_alpha_on_uptrend_with_three_exits(self) -> None:
        session = {
            "session_exit_count": 3,
            "alpha_vs_spy_pct": -0.126,
            "tape_trend": "uptrend",
        }
        result = check_session_alpha(session, spy_returns=0.5)
        self.assertTrue(result["negative_alpha_active_session"])
        self.assertAlmostEqual(result["alpha_vs_spy_pct"], -0.126)

    def test_no_flag_when_alpha_positive(self) -> None:
        session = {
            "session_exit_count": 3,
            "alpha_vs_spy_pct": 0.05,
            "tape_trend": "uptrend",
        }
        result = check_session_alpha(session, spy_returns=0.5)
        self.assertFalse(result["negative_alpha_active_session"])

    def test_no_flag_with_fewer_than_three_exits(self) -> None:
        session = {
            "session_exit_count": 2,
            "alpha_vs_spy_pct": -0.126,
            "tape_trend": "uptrend",
        }
        result = check_session_alpha(session, spy_returns=0.5)
        self.assertFalse(result["negative_alpha_active_session"])

    def test_no_flag_on_downtrend(self) -> None:
        session = {
            "session_exit_count": 3,
            "alpha_vs_spy_pct": -0.126,
            "tape_trend": "downtrend",
        }
        result = check_session_alpha(session, spy_returns=0.5)
        self.assertFalse(result["negative_alpha_active_session"])

    def test_computes_alpha_from_session_and_spy_returns(self) -> None:
        session = {
            "session_exit_count": 3,
            "session_return_pct": 0.2,
            "tape_trend": "uptrend",
        }
        result = check_session_alpha(session, spy_returns=0.5)
        self.assertAlmostEqual(result["alpha_vs_spy_pct"], -0.3)
        self.assertTrue(result["negative_alpha_active_session"])


class TestFinalizeSessionAfterExit(unittest.TestCase):
    def setUp(self) -> None:
        reset_negative_alpha_disabled_symbols()

    def tearDown(self) -> None:
        reset_negative_alpha_disabled_symbols()

    def test_disables_symbol_after_negative_alpha_flag(self) -> None:
        session = {
            "session_exit_count": 3,
            "alpha_vs_spy_pct": -0.126,
            "tape_trend": "uptrend",
            "symbol": "XYZ",
        }
        result = finalize_session_after_exit(session, symbol="XYZ")
        self.assertTrue(result["negative_alpha_active_session"])
        self.assertTrue(is_symbol_disabled_for_negative_alpha("XYZ"))

    def test_skips_disable_before_third_exit(self) -> None:
        session = {
            "session_exit_count": 2,
            "alpha_vs_spy_pct": -0.126,
            "tape_trend": "uptrend",
            "symbol": "XYZ",
        }
        finalize_session_after_exit(session, symbol="XYZ")
        self.assertFalse(is_symbol_disabled_for_negative_alpha("XYZ"))


if __name__ == "__main__":
    unittest.main()
