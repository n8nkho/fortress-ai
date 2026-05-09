import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestFredIngest(unittest.TestCase):
    def test_parse_shapes(self):
        from agents.domain_ingest.fred_ingest import FredIngest

        ing = FredIngest(Path(__file__).resolve().parents[1])
        raw = {
            "VIXCLS": {
                "latest_date": "2026-05-08",
                "latest_value": 18.2,
                "zscore": 0.4,
                "sample_n": 50,
            }
        }
        recs = ing.parse(raw)
        self.assertEqual(len(recs), 1)
        self.assertTrue(ing.validate(recs[0]))


if __name__ == "__main__":
    unittest.main()
