"""CFTC commitments-of-traders — weekly TXT inside fut_fin_txt_<YYYY>.zip."""

from __future__ import annotations

import csv
import io
import logging
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import urllib.request

from agents.domain_ingest.base_ingest import BaseIngest

logger = logging.getLogger("domain_ingest.cot")


def _next_friday_close_utc(now: datetime) -> datetime:
    """Expiry for weekly COT signals: end of next Friday UTC."""
    days_ahead = (4 - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    nf = now + timedelta(days=days_ahead)
    return nf.replace(hour=23, minute=59, second=0, microsecond=0)


def _ua() -> str:
    custom = (os.environ.get("FORTRESS_SEC_USER_AGENT") or "").strip()
    if custom:
        return custom
    return (
        "Mozilla/5.0 (compatible; Fortress-AI/1.0; +mailto:fortress-ai@localhost) "
        "AppleWebKit/537.36 (KHTML, like Gecko)"
    )


def _int_cell(val: str) -> int:
    s = (val or "").strip()
    if not s or s == ".":
        return 0
    try:
        return int(float(s.replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _nets(row: list[str]) -> tuple[int, int, int]:
    """Dealer ~ commercial; Lev_Money ~ large spec; NonRept ~ small retail."""
    dl, ds = _int_cell(row[8]), _int_cell(row[9])
    ll, ls = _int_cell(row[14]), _int_cell(row[15])
    nl, ns = _int_cell(row[22]), _int_cell(row[23])
    return dl - ds, ll - ls, nl - ns


class CotReportIngest(BaseIngest):
    source_name = "cot_report"

    _TARGETS: tuple[tuple[str, str], ...] = (
        ("E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE", "SPX_E_MINI"),
        ("NASDAQ-100 Consolidated - CHICAGO MERCANTILE EXCHANGE", "NDX_CONSOLIDATED"),
        ("VIX FUTURES - CBOE FUTURES EXCHANGE", "VIX_FUT"),
        ("USD INDEX - ICE FUTURES U.S.", "USD_INDEX"),
    )

    def __init__(self, root: Path) -> None:
        self.root = root

    def fetch(self) -> bytes | None:
        year = datetime.now(timezone.utc).year
        url = f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"
        req = urllib.request.Request(url, headers={"User-Agent": _ua()}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception:
            logger.exception("COT zip download failed")
            return None

    def parse(self, raw: bytes | None) -> list[dict[str, Any]]:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        vu = _next_friday_close_utc(now_dt).isoformat()

        if not raw:
            return [
                {
                    "source": self.source_name,
                    "ingested_at": now,
                    "ticker": None,
                    "signal_type": "positioning",
                    "value": {"note": "cot_zip_unavailable"},
                    "confidence": 0.2,
                    "valid_until": vu,
                }
            ]

        recs: list[dict[str, Any]] = []
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
            names = [
                n
                for n in zf.namelist()
                if n.lower().endswith(".csv") or n.lower().endswith(".txt")
            ]
            if not names:
                raise RuntimeError("no_csv_or_txt_in_zip")
            text = zf.read(names[0]).decode("utf-8", errors="replace")
            reader = csv.reader(text.splitlines())
            header = next(reader, None)
            if not header:
                raise RuntimeError("empty_cot_file")

            # Group rows by contract name (multiple report dates per contract)
            by_contract: dict[str, list[list[str]]] = {}
            for row in reader:
                if len(row) < 24:
                    continue
                key = row[0].strip()
                by_contract.setdefault(key, []).append(row)

            for exact_name, short_name in self._TARGETS:
                rows = list(by_contract.get(exact_name, []))
                if not rows:
                    continue
                # Newest report first (YYMMDD in col 1 desc)
                def sort_key(r: list[str]) -> str:
                    return r[1] if len(r) > 1 else ""

                rows_sorted = sorted(rows, key=sort_key, reverse=True)
                latest = rows_sorted[0]
                prev = rows_sorted[1] if len(rows_sorted) > 1 else None

                nc, nls, nss = _nets(latest)
                prev_nls = _nets(prev)[1] if prev else None
                wchg = float(nls - prev_nls) if prev_nls is not None else 0.0

                val = {
                    "contract": latest[0][:120],
                    "report_date": latest[2] if len(latest) > 2 else None,
                    "as_of_yymmdd": latest[1] if len(latest) > 1 else None,
                    "net_commercial": nc,
                    "net_large_spec": nls,
                    "net_small_spec": nss,
                    "large_spec_wow_change": round(wchg, 2),
                    "source_file": names[0],
                }
                rec = {
                    "source": self.source_name,
                    "ingested_at": now,
                    "ticker": None,
                    "signal_type": "positioning",
                    "value": {"contract_key": short_name, **val},
                    "confidence": 0.72 if prev else 0.55,
                    "valid_until": vu,
                }
                if self.validate(rec):
                    recs.append(rec)

            if not recs:
                gold_note = {
                    "note": "no_target_contracts_in_file",
                    "hint": "Gold may appear in other CFTC product files; fut_fin scope is futures/financials only.",
                }
                recs.append(
                    {
                        "source": self.source_name,
                        "ingested_at": now,
                        "ticker": None,
                        "signal_type": "positioning",
                        "value": gold_note,
                        "confidence": 0.25,
                        "valid_until": vu,
                    }
                )

        except Exception:
            logger.exception("COT parse failed")
            rec = {
                "source": self.source_name,
                "ingested_at": now,
                "ticker": None,
                "signal_type": "positioning",
                "value": {"note": "parse_error"},
                "confidence": 0.2,
                "valid_until": vu,
            }
            if self.validate(rec):
                recs.append(rec)
        return recs
