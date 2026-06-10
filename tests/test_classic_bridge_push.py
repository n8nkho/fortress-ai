"""Fortress → Classic queue bridge tests."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.classic_bridge import push_findings_to_classic_queue, resolve_trading_bot_root


class TestClassicBridgePush(unittest.TestCase):
    def test_resolve_trading_bot_root(self):
        root = resolve_trading_bot_root()
        self.assertTrue(root is None or root.is_dir())

    def test_push_no_root_returns_empty(self):
        with patch("utils.classic_bridge.resolve_trading_bot_root", return_value=None):
            out = push_findings_to_classic_queue(
                [{"component": "classic_fortress", "objective_id": "classic_fill_recency"}],
                [],
            )
        self.assertEqual(out, [])

    def test_push_skips_non_classic_gaps(self):
        with patch("utils.classic_bridge.resolve_trading_bot_root", return_value=None):
            out = push_findings_to_classic_queue(
                [{"component": "skim_swarm", "objective_id": "x"}],
                [],
            )
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
