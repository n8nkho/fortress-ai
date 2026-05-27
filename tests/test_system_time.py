"""Tests for canonical US/Eastern system time."""
from __future__ import annotations

import os
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.system_time import (
    ensure_system_tz,
    now,
    now_iso,
    parse_iso,
    system_tz_name,
)


class TestSystemTime(unittest.TestCase):
    def setUp(self):
        os.environ.pop("FORTRESS_SYSTEM_TZ", None)

    def test_default_tz_is_new_york(self):
        self.assertEqual(system_tz_name(), "America/New_York")

    def test_now_has_new_york_offset(self):
        dt = now()
        self.assertIsNotNone(dt.tzinfo)
        self.assertEqual(dt.tzinfo, ZoneInfo("America/New_York"))

    def test_now_iso_roundtrip(self):
        raw = now_iso()
        parsed = parse_iso(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.tzinfo, ZoneInfo("America/New_York"))

    def test_ensure_system_tz_sets_env(self):
        ensure_system_tz()
        self.assertEqual(os.environ.get("FORTRESS_SYSTEM_TZ"), "America/New_York")
        self.assertEqual(os.environ.get("TZ"), "America/New_York")

    def test_env_override(self):
        os.environ["FORTRESS_SYSTEM_TZ"] = "America/Chicago"
        self.assertEqual(system_tz_name(), "America/Chicago")


if __name__ == "__main__":
    unittest.main()
