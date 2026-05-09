import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestBaseIngest(unittest.TestCase):
    def test_validate_requires_ticker_key(self):
        from agents.domain_ingest.base_ingest import BaseIngest

        class X(BaseIngest):
            source_name = "x"

            def fetch(self):
                return None

            def parse(self, raw):
                return []

        x = X()
        ok = {
            "source": "x",
            "ingested_at": "t",
            "ticker": None,
            "signal_type": "macro",
            "value": {},
            "confidence": 0.5,
            "valid_until": "t2",
        }
        self.assertTrue(x.validate(ok))


if __name__ == "__main__":
    unittest.main()
