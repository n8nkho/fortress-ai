"""Domain knowledge store + intel snapshot."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestDomainKnowledge(unittest.TestCase):
    def test_init_creates_domains(self):
        from knowledge.domain_knowledge import DomainKnowledge

        td = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        dk = DomainKnowledge(root=td)
        mr = dk.get_domain("market_regimes")
        self.assertIn("NEUTRAL_RANGING", mr)
        self.assertIn("mean_reversion", dk.get_domain("trading_strategies"))

    def test_get_relevant_knowledge(self):
        from knowledge.domain_knowledge import DomainKnowledge

        td = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        dk = DomainKnowledge(root=td)
        rel = dk.get_relevant_knowledge(
            {"regime": "NEUTRAL_RANGING", "sector": "technology", "strategy": "mean_reversion"}
        )
        self.assertIn("regime_knowledge", rel)
        self.assertIn("strategy_knowledge", rel)
        self.assertTrue(rel["sector_knowledge"])

    def test_domain_intel_snapshot(self):
        from knowledge.intel import domain_intel_snapshot

        td = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        macro = {"spy": 100.0, "vix": 17.0, "rsi": 55.0}
        snap = domain_intel_snapshot(macro, beliefs={}, root=td)
        self.assertIn("regime_hint", snap)
        self.assertEqual(snap.get("rsi_hint"), 55.0)


if __name__ == "__main__":
    unittest.main()
