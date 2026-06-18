"""Portfolio session entry block tracking and reporting."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.portfolio_session.entry_manager import EntryManager, record_entry_block
from utils.portfolio_session.reporting import format_session_report, log_entry_block_report
from utils.portfolio_session.session_summary import generate_summary


class TestEntryManager(unittest.TestCase):
    def setUp(self) -> None:
        self.em = EntryManager()

    def test_denylist_increment(self) -> None:
        self.em.evaluate_entry_blocks("manual_denylist", side="flat", action="wait")
        self.assertEqual(self.em.block_counts()["denylist"], 1)

    def test_pause_entries_increment(self) -> None:
        self.em.evaluate_entry_blocks("pause_entries", side="flat", action="wait")
        self.assertEqual(self.em.block_counts()["pause_entries"], 1)

    def test_pattern_disables_increment(self) -> None:
        self.em.evaluate_entry_blocks("pattern_disabled:momentum", side="flat", action="wait")
        self.assertEqual(self.em.block_counts()["pattern_disables"], 1)

    def test_ignores_non_flat_side(self) -> None:
        self.em.evaluate_entry_blocks("manual_denylist", side="long", action="wait")
        self.assertEqual(sum(self.em.block_counts().values()), 0)

    def test_record_entry_block_from_worker_payload(self) -> None:
        em = EntryManager()
        with patch("utils.portfolio_session.entry_manager.get_entry_manager", return_value=em):
            block = record_entry_block(
                {"action": "wait", "reasoning": "manual_denylist"},
                {"executed": False, "block_reason": "manual_denylist"},
                features={"side": "flat"},
            )
        self.assertEqual(block, "denylist")
        self.assertEqual(em.block_counts()["denylist"], 1)


class TestSessionSummary(unittest.TestCase):
    def test_generate_summary_replays_signals(self) -> None:
        em = EntryManager()
        signals = [
            {
                "features": {"side": "flat"},
                "decision": {"action": "wait", "reasoning": "manual_denylist"},
                "act": {"executed": False, "block_reason": "manual_denylist"},
            },
            {
                "features": {"side": "flat"},
                "decision": {"action": "wait", "reasoning": "pause_entries"},
                "act": {"executed": False},
            },
            {
                "features": {"side": "flat"},
                "decision": {"action": "wait", "reasoning": "pattern_disabled:rsi"},
                "act": {"executed": False},
            },
        ]
        summary = generate_summary(signals=signals, entry_manager=em)
        breakdown = summary["entry_block_breakdown"]
        self.assertEqual(breakdown["denylist"], 1)
        self.assertEqual(breakdown["pause_entries"], 1)
        self.assertEqual(breakdown["pattern_disables"], 1)


class TestSessionReporting(unittest.TestCase):
    def test_format_report_zero_exits_positive_benchmark(self) -> None:
        line = format_session_report(
            {"entry_block_breakdown": {"denylist": 3, "pause_entries": 0, "pattern_disables": 5}},
            portfolio={"session_exit_count": 0},
            benchmark={"change_1d_pct": 0.42},
        )
        self.assertEqual(line, "Entry blocks active: denylist=3, pause_entries=0, pattern_disables=5")

    def test_format_report_skips_when_exits_positive(self) -> None:
        line = format_session_report(
            {"entry_block_breakdown": {"denylist": 1, "pause_entries": 0, "pattern_disables": 0}},
            portfolio={"session_exit_count": 2},
            benchmark={"change_1d_pct": 0.42},
        )
        self.assertIsNone(line)

    @patch("utils.portfolio_session.reporting._LOG")
    def test_log_entry_block_report_emits_info(self, mock_log) -> None:
        line = log_entry_block_report(
            {"entry_block_breakdown": {"denylist": 2, "pause_entries": 1, "pattern_disables": 0}},
            portfolio={"session_exit_count": 0},
            benchmark={"change_1d_pct": 0.1},
        )
        self.assertIn("Entry blocks active", line or "")
        mock_log.info.assert_called_once()


class TestMarketBenchmarkIntegration(unittest.TestCase):
    @patch("utils.portfolio_session.reporting.log_entry_block_report")
    @patch("utils.portfolio_session.session_summary.generate_summary")
    @patch("utils.market_benchmark._session_combined_realized_usd", return_value=(0.0, 0))
    def test_build_metrics_includes_entry_block_breakdown(self, _pnl, mock_summary, _log) -> None:
        from utils.market_benchmark import build_portfolio_session_metrics

        mock_summary.return_value = {
            "entry_block_breakdown": {"denylist": 4, "pause_entries": 0, "pattern_disables": 2},
        }
        bench = {"ok": True, "benchmark": "SPY", "change_1d_pct": 0.5, "strong_tape_1d": True}
        port = build_portfolio_session_metrics(benchmark=bench, reference_equity_usd=100_000.0)
        self.assertEqual(port["entry_block_breakdown"]["denylist"], 4)
        self.assertEqual(port["entry_block_breakdown"]["pattern_disables"], 2)


if __name__ == "__main__":
    unittest.main()
