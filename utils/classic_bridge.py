"""Read-only bridge to Classic Fortress (trading-bot) data and optional Alpaca account."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.alpaca_env import _strip_env_cred


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_classic_data_dir() -> Path | None:
    raw = (os.environ.get("CLASSIC_DATA_DIR") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_dir() else None
    tb = (os.environ.get("FORTRESS_TRADING_BOT_ROOT") or "").strip()
    if tb:
        p = Path(tb).expanduser() / "data"
        return p if p.is_dir() else None
    sibling = _repo_root().parent / "trading-bot" / "data"
    return sibling if sibling.is_dir() else None


def _parse_dotenv_keys(path: Path, keys: frozenset[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k in keys:
                out[k] = _strip_env_cred(v)
    except OSError:
        pass
    return out


def classic_alpaca_credentials() -> tuple[str, str, str]:
    """Classic Alpaca key, secret, base URL without overwriting Fortress env."""
    key = _strip_env_cred(os.environ.get("CLASSIC_ALPACA_API_KEY"))
    sec = _strip_env_cred(os.environ.get("CLASSIC_ALPACA_SECRET_KEY"))
    base = (os.environ.get("CLASSIC_ALPACA_BASE_URL") or "").strip().rstrip("/")
    if key and sec:
        return key, sec, base
    env_file = (os.environ.get("CLASSIC_ENV_FILE") or "").strip()
    if not env_file:
        sibling = _repo_root().parent / "trading-bot" / ".env"
        env_file = str(sibling) if sibling.is_file() else ""
    if env_file:
        picked = _parse_dotenv_keys(
            Path(env_file).expanduser(),
            frozenset({"ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL"}),
        )
        key = key or picked.get("ALPACA_API_KEY", "")
        sec = sec or picked.get("ALPACA_SECRET_KEY", "")
        base = base or (picked.get("ALPACA_BASE_URL") or "").strip().rstrip("/")
    return key, sec, base


def classic_alpaca_snapshot() -> dict[str, Any]:
    key, sec, base = classic_alpaca_credentials()
    if not key or not sec:
        return {"connected": False, "reason": "classic_keys_missing"}
    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        return {"connected": False, "reason": "alpaca_sdk_missing"}
    paper = "paper" in (base or "").lower() if base else True
    kw: dict[str, Any] = {"paper": paper}
    if base:
        kw["url_override"] = base
    try:
        tc = TradingClient(key, sec, **kw)
        acct = tc.get_account()
        pos = tc.get_all_positions()
        positions = [
            {
                "symbol": getattr(p, "symbol", ""),
                "qty": float(getattr(p, "qty", 0) or 0),
                "market_value": float(getattr(p, "market_value", 0) or 0),
                "unrealized_pl": float(getattr(p, "unrealized_pl", 0) or 0),
            }
            for p in pos[:40]
        ]
        unreal = sum(float(p.get("unrealized_pl") or 0) for p in positions)
        return {
            "connected": True,
            "paper": paper,
            "equity": float(acct.equity),
            "buying_power": float(getattr(acct, "buying_power", 0) or 0),
            "position_count": len(positions),
            "unrealized_pl": round(unreal, 2),
            "positions": positions,
        }
    except Exception as e:
        return {"connected": False, "reason": f"{type(e).__name__}:{e}"[:240]}


def _tickers_from_watchlist_file(path: Path, *, max_n: int) -> list[str]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    syms: list[str] = []
    if isinstance(raw, list):
        for x in raw:
            t = str(x).strip().upper()
            if t and t not in syms:
                syms.append(t[:12])
    elif isinstance(raw, dict):
        tiers = raw.get("priority_tiers") or raw.get("quality_stocks")
        if isinstance(tiers, dict):
            for items in tiers.values():
                if isinstance(items, list):
                    for x in items:
                        t = str(x).strip().upper()
                        if t and t not in syms:
                            syms.append(t[:12])
        elif isinstance(tiers, list):
            for x in tiers:
                t = str(x).strip().upper()
                if t and t not in syms:
                    syms.append(t[:12])
        for key in ("symbols", "tickers", "watchlist"):
            v = raw.get(key)
            if isinstance(v, list):
                for x in v:
                    t = str(x).strip().upper()
                    if t and t not in syms:
                        syms.append(t[:12])
    return syms[:max_n]


def _newest_daily_signals_path(data_dir: Path) -> Path | None:
    files = sorted(data_dir.glob("daily_signals_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def classic_screener_candidates(*, max_symbols: int = 12) -> dict[str, Any]:
    """
    Fallback universe for Active scan: Classic daily_signals → fortress watchlist → Classic config watchlist.
    """
    max_symbols = max(1, min(int(max_symbols), 24))
    data_dir = resolve_classic_data_dir()
    root = _repo_root()

    if data_dir:
        sig_path = _newest_daily_signals_path(data_dir)
        if sig_path:
            try:
                doc = json.loads(sig_path.read_text(encoding="utf-8"))
                cands = doc.get("candidates") or []
                syms: list[str] = []
                for c in cands:
                    if not isinstance(c, dict):
                        continue
                    t = str(c.get("ticker") or c.get("symbol") or "").strip().upper()
                    if t and t not in syms:
                        syms.append(t[:12])
                if syms:
                    ts = doc.get("timestamp") or datetime.fromtimestamp(
                        sig_path.stat().st_mtime, tz=timezone.utc
                    ).isoformat()
                    return {
                        "symbols": syms[:max_symbols],
                        "ts": ts,
                        "source": "classic_daily_signals",
                        "path": str(sig_path),
                    }
            except Exception:
                pass

    wl = _tickers_from_watchlist_file(root / "data" / "watchlist.json", max_n=max_symbols)
    if wl:
        return {"symbols": wl, "ts": None, "source": "fortress_watchlist", "path": str(root / "data" / "watchlist.json")}

    tb = (os.environ.get("FORTRESS_TRADING_BOT_ROOT") or "").strip()
    cfg = Path(tb).expanduser() / "config" / "watchlist.json" if tb else root.parent / "trading-bot" / "config" / "watchlist.json"
    wl2 = _tickers_from_watchlist_file(cfg, max_n=max_symbols)
    if wl2:
        return {"symbols": wl2, "ts": None, "source": "classic_config_watchlist", "path": str(cfg)}

    return {"symbols": [], "ts": None, "source": None}


def symbols_from_ai_decision_rows(rows: list[dict]) -> set[str]:
    syms: set[str] = set()
    for r in rows:
        d = r.get("decision")
        if not isinstance(d, dict):
            continue
        p = d.get("parameters") if isinstance(d.get("parameters"), dict) else {}
        s = p.get("symbol") or p.get("sym")
        if s:
            syms.add(str(s).strip().upper()[:12])
        action = (d.get("action") or "").lower()
        if action == "screen_market":
            wl = p.get("watchlist") or p.get("symbols") or p.get("tickers")
            if isinstance(wl, list):
                for x in wl:
                    t = str(x).strip().upper()
                    if t:
                        syms.add(t[:12])
            elif isinstance(wl, str) and wl.strip():
                for part in re.split(r"[\s,;]+", wl):
                    t = part.strip().upper()
                    if t:
                        syms.add(t[:12])
    return syms
