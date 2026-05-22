"""Historical skim verification (daily proxy)."""
from __future__ import annotations

import unittest

from agents.skim_swarm.historical_verify import SimConfig, _summarize


class TestHistoricalVerify(unittest.TestCase):
    def test_summarize_empty(self):
        s = _summarize([])
        self.assertEqual(s["trades"], 0)

    def test_sim_config_defaults(self):
        c = SimConfig()
        self.assertEqual(c.years, 10)


if __name__ == "__main__":
    unittest.main()
