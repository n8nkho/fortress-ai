"""Ticker headline sentiment via NewsAPI or RSS fallback."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agents.domain_ingest.base_ingest import BaseIngest

logger = logging.getLogger("domain_ingest.news")

_POS = re.compile(
    r"\b(beat|raised|upgrade|growth|record|strong|buyback|dividend)\b", re.I
)
_NEG = re.compile(
    r"\b(miss|cut|downgrade|loss|recall|investigation|layoff|warning)\b", re.I
)


def _watchlist(root: Path) -> list[str]:
    p = root / "data" / "watchlist.json"
    if not p.exists():
        return ["SPY", "AAPL"]
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, list):
            return [str(x).strip().upper() for x in doc][:40]
        if isinstance(doc, dict) and doc.get("tickers"):
            return [str(x).strip().upper() for x in doc["tickers"]][:40]
    except Exception:
        pass
    return ["SPY", "AAPL"]


def _fetch_newsapi(sym: str, api_key: str) -> list[str]:
    q = urllib.parse.urlencode(
        {
            "q": sym,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 20,
            "apiKey": api_key,
        }
    )
    url = f"https://newsapi.org/v2/everything?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "Fortress-AI/1.0"}, method="GET")
    with urllib.request.urlopen(req, timeout=25) as resp:
        doc = json.loads(resp.read().decode("utf-8", errors="replace"))
    arts = doc.get("articles") or []
    headlines: list[str] = []
    for a in arts:
        t = (a.get("title") or "") + " " + (a.get("description") or "")
        headlines.append(t.strip())
    return headlines


def _fetch_rss_fallback() -> list[str]:
    url = "https://feeds.reuters.com/reuters/businessNews"
    req = urllib.request.Request(url, headers={"User-Agent": "Fortress-AI/1.0"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=22) as resp:
            txt = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    root = ET.fromstring(txt)
    titles = []
    for item in root.iter():
        if item.tag.endswith("title") and item.text:
            titles.append(item.text.strip())
    return titles[:25]


def _score(headlines: list[str], sym: str) -> dict[str, Any]:
    pos = neg = 0
    matched: list[str] = []
    for h in headlines:
        if sym.upper() not in h.upper() and sym != "SPY":
            continue
        matched.append(h[:120])
        pos += len(_POS.findall(h))
        neg += len(_NEG.findall(h))
    tot = pos + neg + 1
    score = (pos - neg) / tot
    score = max(-1.0, min(1.0, score * 3.0))
    top = matched[0] if matched else (headlines[0][:120] if headlines else "")
    return {
        "headline_count": len(headlines),
        "positive_hits": pos,
        "negative_hits": neg,
        "sentiment_score": round(score, 4),
        "top_headline": top,
    }


class NewsSentimentIngest(BaseIngest):
    source_name = "news_sentiment"

    def __init__(self, root: Path) -> None:
        self.root = root

    def fetch(self) -> dict[str, Any]:
        key = (os.environ.get("NEWS_API_KEY") or "").strip()
        syms = _watchlist(self.root)
        out: dict[str, Any] = {}
        if key:
            for sym in syms:
                try:
                    out[sym] = _fetch_newsapi(sym, key)
                except Exception:
                    logger.exception("newsapi failed for %s", sym)
                    out[sym] = []
        else:
            logger.warning("NEWS_API_KEY missing — using generic RSS fallback")
            fh = _fetch_rss_fallback()
            for sym in syms:
                out[sym] = fh
        return {"by_symbol": out, "symbols": syms}

    def parse(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        vu = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
        recs: list[dict[str, Any]] = []
        by_sym = raw.get("by_symbol") or {}
        for sym in raw.get("symbols") or []:
            headlines = by_sym.get(sym) or []
            val = _score(headlines, sym)
            rec = {
                "source": self.source_name,
                "ingested_at": now,
                "ticker": sym,
                "signal_type": "news_sentiment",
                "value": val,
                "confidence": min(1.0, 0.35 + abs(val["sentiment_score"]) * 0.5),
                "valid_until": vu,
            }
            if self.validate(rec):
                recs.append(rec)
        return recs
