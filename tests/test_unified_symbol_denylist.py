"""Unified AI symbol denylist includes infra swarm universe."""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestUnifiedSymbolDenylist(unittest.TestCase):
    def test_denylist_includes_infra_universe(self):
        from utils.skim_swarm_config import symbol_denylist_for_unified_ai

        with patch("utils.skim_swarm_config.universe", return_value=["SPY"]):
            with patch("utils.infra_swarm_config.universe", return_value=["SMH", "NVDA"]):
                deny = symbol_denylist_for_unified_ai()
        self.assertIn("SPY", deny)
        self.assertIn("SMH", deny)
        self.assertIn("NVDA", deny)

    def test_denylist_includes_infra_anchor_when_not_in_active_universe(self):
        from utils.skim_swarm_config import symbol_denylist_for_unified_ai

        with patch("utils.skim_swarm_config.universe", return_value=["SPY"]):
            with patch("utils.infra_swarm_config.universe", return_value=["NVDA"]):
                with patch("utils.infra_swarm_config.anchor_symbol", return_value="SMH"):
                    deny = symbol_denylist_for_unified_ai()
        self.assertIn("SMH", deny)
        self.assertIn("NVDA", deny)
        self.assertIn("SPY", deny)

    def test_live_denylist_includes_smh_anchor(self):
        from utils.skim_swarm_config import symbol_denylist_for_unified_ai

        deny = symbol_denylist_for_unified_ai()
        self.assertIn("SMH", deny)


if __name__ == "__main__":
    unittest.main()
