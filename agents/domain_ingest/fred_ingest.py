"""FRED macro series (daily) — FRED_API_KEY preferred; Alpha Vantage fallback."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agents.domain_ingest.base_ingest import BaseIngest

logger = logging.getLogger("domain_ingest.fred")

ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"


def _series_list() -> list[str]:
    raw = os.getenv("FORTRESS_FRED_SERIES", "VIXCLS,DGS10,T10Y2Y,UMCSENT,DCOILWTICO")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _av_call_delay_sec() -> float:
    return float(os.getenv("FORTRESS_ALPHA_VANTAGE_DELAY_SEC", "0") or "0")


def _http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "fortress-ai-domain-ingest/1"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _compute_payload(vals: list[float], latest_val: float | None, latest_date: str | None) -> dict[str, Any] | None:
    if latest_val is None or not vals:
        return None
    mu = sum(vals) / len(vals)
    var = sum((x - mu) ** 2 for x in vals) / max(len(vals) - 1, 1)
    std = var**0.5
    z = (latest_val - mu) / std if std > 1e-12 else 0.0
    return {
        "latest_date": latest_date,
        "latest_value": latest_val,
        "zscore": round(z, 4),
        "sample_n": len(vals),
    }


def _fred_observations_to_payload(obs: list[dict[str, Any]]) -> dict[str, Any] | None:
    vals: list[float] = []
    latest_val = None
    latest_date = None
    for o in obs:
        if str(o.get("value")) == ".":
            continue
        try:
            v = float(o.get("value"))
        except (TypeError, ValueError):
            continue
        vals.append(v)
        if latest_val is None:
            latest_val = v
            latest_date = o.get("date")
        if len(vals) >= 260:
            break
    return _compute_payload(vals, latest_val, str(latest_date) if latest_date else None)


def _fetch_fred_series(series_id: str, api_key: str) -> dict[str, Any] | None:
    url = (
        "https://api.stlouisfed.org/fred/series/observations?"
        + urllib.parse.urlencode(
            {
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 400,
            }
        )
    )
    doc = _http_json(url)
    obs = doc.get("observations") or []
    return _fred_observations_to_payload(obs)


def _parse_alpha_vantage_data(doc: dict[str, Any]) -> list[tuple[str, float]]:
    """Alpha Vantage economic / commodity JSON → (date, value) sorted newest first."""
    msg = doc.get("Error Message") or doc.get("Information") or doc.get("Note")
    if msg:
        logger.warning("Alpha Vantage: %s", str(msg)[:300])
        return []
    data = doc.get("data")
    rows: list[tuple[str, float]] = []
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            ds = row.get("date")
            vs = row.get("value")
            if ds is None or vs is None:
                continue
            try:
                v = float(vs)
            except (TypeError, ValueError):
                continue
            rows.append((str(ds), v))
        rows.sort(key=lambda x: x[0], reverse=True)
        return rows
    return []


def _series_desc_to_payload(series: list[tuple[str, float]]) -> dict[str, Any] | None:
    vals: list[float] = []
    latest_val = None
    latest_date = None
    for ds, v in series:
        vals.append(v)
        if latest_val is None:
            latest_val = v
            latest_date = ds
        if len(vals) >= 260:
            break
    return _compute_payload(vals, latest_val, latest_date)


def _alpha_vantage_treasury_daily(api_key: str, maturity: str) -> list[tuple[str, float]]:
    q = urllib.parse.urlencode(
        {
            "function": "TREASURY_YIELD",
            "interval": "daily",
            "maturity": maturity,
            "apikey": api_key,
        }
    )
    url = f"{ALPHA_VANTAGE_BASE}?{q}"
    doc = _http_json(url)
    return _parse_alpha_vantage_data(doc)


def _alpha_vantage_wti_daily(api_key: str) -> list[tuple[str, float]]:
    q = urllib.parse.urlencode({"function": "WTI", "interval": "daily", "apikey": api_key})
    url = f"{ALPHA_VANTAGE_BASE}?{q}"
    doc = _http_json(url)
    return _parse_alpha_vantage_data(doc)


def _spread_series(
    a: list[tuple[str, float]], b: list[tuple[str, float]]
) -> list[tuple[str, float]]:
    ma = {d: v for d, v in a}
    mb = {d: v for d, v in b}
    common = sorted(set(ma) & set(mb), reverse=True)
    return [(d, ma[d] - mb[d]) for d in common]


def _fetch_alpha_vantage(api_key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    delay = _av_call_delay_sec()
    wanted = _series_list()
    want = set(wanted)

    for sid in wanted:
        if sid == "VIXCLS":
            logger.info("skipping %s — use market/yfinance VIX elsewhere", sid)
        elif sid == "UMCSENT":
            logger.info("skipping %s — not available on Alpha Vantage (UMich sentiment)", sid)
        elif sid not in {"DGS10", "T10Y2Y", "DCOILWTICO"}:
            logger.debug("no Alpha Vantage mapping for FRED series %s — skipped", sid)

    s10: list[tuple[str, float]] | None = None
    if want & {"DGS10", "T10Y2Y"}:
        s10 = _alpha_vantage_treasury_daily(api_key, "10year")
        if delay > 0:
            time.sleep(delay)

    if "DGS10" in want and s10 is not None:
        pl = _series_desc_to_payload(s10)
        if pl:
            out["DGS10"] = pl

    if "T10Y2Y" in want and s10 is not None:
        s2 = _alpha_vantage_treasury_daily(api_key, "2year")
        if delay > 0:
            time.sleep(delay)
        spread = _spread_series(s10, s2)
        pl = _series_desc_to_payload(spread)
        if pl:
            out["T10Y2Y"] = pl

    if "DCOILWTICO" in want:
        wti = _alpha_vantage_wti_daily(api_key)
        pl = _series_desc_to_payload(wti)
        if pl:
            out["DCOILWTICO"] = pl

    return out


class FredIngest(BaseIngest):
    source_name = "fred"

    def __init__(self, root: Path) -> None:
        self.root = root

    def fetch(self) -> dict[str, Any]:
        fred_key = (os.environ.get("FRED_API_KEY") or "").strip()
        if fred_key:
            logger.info(
                "macro ingest: using St. Louis Fed API (FRED_API_KEY set — Alpha Vantage fallback not used)"
            )
            return self._fetch_fred(fred_key)

        av_key = (os.environ.get("ALPHA_VANTAGE_KEY") or "").strip()
        if av_key:
            logger.info("FRED_API_KEY not set — using Alpha Vantage fallback for macro series")
            return _fetch_alpha_vantage(av_key)

        logger.warning("no macro API key configured (set FRED_API_KEY or ALPHA_VANTAGE_KEY)")
        return {}

    def _fetch_fred(self, api_key: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for sid in _series_list():
            pl = _fetch_fred_series(sid, api_key)
            if pl:
                out[sid] = pl
        return out

    def parse(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        valid_until = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        recs: list[dict[str, Any]] = []
        for sid, payload in raw.items():
            rec = {
                "source": self.source_name,
                "ingested_at": now,
                "ticker": None,
                "signal_type": "macro",
                "value": {"series_id": sid, **payload},
                "confidence": min(1.0, max(0.3, 0.5 + abs(float(payload.get("zscore") or 0)) * 0.1)),
                "valid_until": valid_until,
            }
            if self.validate(rec):
                recs.append(rec)
        return recs
