"""Governance tiers and shadow helper tests."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestGovernanceTiers(unittest.TestCase):
    def test_tier_3_blocks_immutable_names(self):
        from utils.improvement_governance import determine_governance_tier

        self.assertEqual(determine_governance_tier("max_position_size_pct"), "tier_3_blocked")

    def test_tier_0_low_risk_params(self):
        from utils.improvement_governance import determine_governance_tier

        self.assertEqual(determine_governance_tier("confidence_threshold"), "tier_0_auto")
        self.assertEqual(determine_governance_tier("decision_interval"), "tier_0_auto")

    def test_tier_1_medium_params(self):
        from utils.improvement_governance import determine_governance_tier

        self.assertEqual(determine_governance_tier("rsi_entry_threshold"), "tier_1_notify")


if __name__ == "__main__":
    unittest.main()
