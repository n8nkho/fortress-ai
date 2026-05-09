"""SEC EDGAR Atom feed for 8-K announcements (metadata only)."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agents.domain_ingest.base_ingest import BaseIngest

logger = logging.getLogger("domain_ingest.sec_edgar")

_ATOM_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K"
    "&dateb=&owner=include&count=20&output=atom"
)


def _ua() -> str:
    """SEC.gov blocks generic agents; use a browser-style UA + optional operator override."""
    custom = (os.environ.get("FORTRESS_SEC_USER_AGENT") or "").strip()
    if custom:
        return custom
    return (
        "Mozilla/5.0 (compatible; Fortress-AI/1.0; +mailto:fortress-ai@localhost) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Python-urllib compatible"
    )


def _load_watchlist(root: Path) -> set[str]:
    p = root / "data" / "watchlist.json"
    if not p.exists():
        return set()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, list):
            return {str(x).strip().upper() for x in doc}
        if isinstance(doc, dict) and doc.get("tickers"):
            return {str(x).strip().upper() for x in doc["tickers"]}
    except Exception:
        logger.exception("watchlist read failed")
    return set()


def _add_trading_days(start: datetime, n: int) -> datetime:
    """US weekday-only roll (Mon–Fri); excludes start day from the count."""
    cur = start.date()
    left = max(0, int(n))
    while left > 0:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            left -= 1
    return datetime(cur.year, cur.month, cur.day, tzinfo=timezone.utc)


def _guess_ticker_from_title(title: str, wl: set[str]) -> str | None:
    if not title:
        return None
    for sym in wl:
        if sym and sym in title.upper():
            return sym
    m = re.search(r"\(([A-Z]{1,5})\)", title)
    if m and m.group(1) in wl:
        return m.group(1)
    return None


class SecEdgarIngest(BaseIngest):
    source_name = "sec_edgar"

    def __init__(self, root: Path) -> None:
        self.root = root

    def fetch(self) -> str:
        req = urllib.request.Request(
            _ATOM_URL,
            headers={
                "User-Agent": _ua(),
                "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=35) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def parse(self, raw: str) -> list[dict[str, Any]]:
        wl = _load_watchlist(self.root)
        try:
            root_el = ET.fromstring(raw)
        except Exception:
            logger.exception("SEC EDGAR atom XML parse failed")
            return []
        entries = [el for el in root_el.iter() if el.tag.endswith("entry")]
        now = datetime.now(timezone.utc).isoformat()
        out: list[dict[str, Any]] = []
        for ent in entries:
            title = updated = ""
            href = ""
            for ch in list(ent):
                tag = ch.tag.split("}")[-1]
                if tag == "title" and ch.text:
                    title = ch.text.strip()
                elif tag == "updated" and ch.text:
                    updated = ch.text.strip()
                elif tag == "link":
                    href = ch.get("href") or href
            ticker_guess = _guess_ticker_from_title(title, wl)
            relevant = ticker_guess is not None
            filing_dt = updated[:10] if len(updated) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
            try:
                fd = datetime.strptime(filing_dt, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                fd = datetime.now(timezone.utc)
            vu_dt = _add_trading_days(fd, 5)
            valid_until = vu_dt.isoformat()
            val = {
                "company_hint": title[:180],
                "filing_url": href[:500],
                "form": "8-K",
                "relevant": relevant,
                "matched_ticker": ticker_guess,
            }
            rec = {
                "source": self.source_name,
                "ingested_at": now,
                "ticker": ticker_guess,
                "signal_type": "earnings",
                "value": val,
                "confidence": 0.75 if relevant else 0.35,
                "valid_until": valid_until,
            }
            if self.validate(rec):
                out.append(rec)
        return out
