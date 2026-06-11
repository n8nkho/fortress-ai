"""Optional dependency guards for unittest collection (no pytest required)."""
from __future__ import annotations

import importlib.util
import unittest


def has_yfinance() -> bool:
    return importlib.util.find_spec("yfinance") is not None


def skip_if_no_yfinance() -> None:
    if not has_yfinance():
        raise unittest.SkipTest("yfinance not installed (pip install -r requirements.txt)")
