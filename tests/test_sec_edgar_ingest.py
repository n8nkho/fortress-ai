import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>AAPL 8-K Test Company (AAPL)</title>
    <updated>2026-05-09T12:00:00Z</updated>
    <link href="https://sec.gov/example"/>
  </entry>
</feed>
"""


class TestSecEdgar(unittest.TestCase):
    def test_parse(self):
        from agents.domain_ingest.sec_edgar_ingest import SecEdgarIngest

        root = Path(__file__).resolve().parents[1]
        ing = SecEdgarIngest(root)
        recs = ing.parse(_ATOM)
        self.assertTrue(recs)
        self.assertEqual(recs[0]["signal_type"], "earnings")
        self.assertTrue(ing.validate(recs[0]))


if __name__ == "__main__":
    unittest.main()
