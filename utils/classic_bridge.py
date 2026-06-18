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


def resolve_classic_pnl_ledger_path() -> Path | None:
    raw = (os.environ.get("CLASSIC_PNL_LEDGER_PATH") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_file() else None
    data_dir = resolve_classic_data_dir()
    if not data_dir:
        return None
    p = data_dir / "pnl_ledger.jsonl"
    return p if p.is_file() else None


def read_pnl_ledger_summary(path: Path | None) -> dict[str, Any]:
    """Realized P&L from authoritative sell ledger (same schema as Classic command center)."""
    summary: dict[str, Any] = {
        "count": 0,
        "wins": 0,
        "losses": 0,
        "realized_pnl": 0.0,
        "win_rate": None,
        "source": None,
    }
    if path is None or not path.is_file():
        return summary
    summary["source"] = str(path)
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    pnl = float(rec.get("pnl"))
                except (TypeError, ValueError):
                    continue
                summary["count"] += 1
                summary["realized_pnl"] += pnl
                if pnl > 0:
                    summary["wins"] += 1
                elif pnl < 0:
                    summary["losses"] += 1
    except OSError:
        pass
    summary["realized_pnl"] = round(float(summary["realized_pnl"]), 2)
    if summary["count"]:
        summary["win_rate"] = round(summary["wins"] / summary["count"], 4)
    return summary


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


def _classic_session_date_et(ts_raw: str) -> str | None:
    from utils.swarm_decisions_pnl import wave_session_date_et

    return wave_session_date_et(ts_raw)


def classic_rolling_metrics(*, window_sessions: int = 10) -> dict[str, Any]:
    """Rolling Classic Fortress outcomes from sibling trading-bot data (read-only)."""
    from collections import defaultdict

    data_dir = resolve_classic_data_dir()
    ledger = resolve_classic_pnl_ledger_path()
    by_day: dict[str, dict[str, Any]] = defaultdict(lambda: {"realized_usd": 0.0, "exit_count": 0, "wins": 0})
    last_fill_day: str | None = None

    if ledger and ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                pnl = float(rec.get("pnl"))
            except (TypeError, ValueError):
                continue
            day = _classic_session_date_et(str(rec.get("timestamp") or ""))
            if not day:
                continue
            by_day[day]["realized_usd"] = round(float(by_day[day]["realized_usd"]) + pnl, 4)
            by_day[day]["exit_count"] = int(by_day[day]["exit_count"]) + 1
            if pnl > 0:
                by_day[day]["wins"] = int(by_day[day]["wins"]) + 1
            last_fill_day = day

    fill_days = sorted(by_day.keys())[-window_sessions:]
    total_pnl = sum(float(by_day[d]["realized_usd"]) for d in fill_days)
    total_fills = sum(int(by_day[d]["exit_count"]) for d in fill_days)
    expectancy = (total_pnl / total_fills) if total_fills else None

    screens: list[dict[str, Any]] = []
    avg_candidates = None
    latest_regime = None
    if data_dir and data_dir.is_dir():
        sig_files = sorted(data_dir.glob("daily_signals_*.json"), reverse=True)[:window_sessions]
        cand_vals: list[float] = []
        for path in sig_files:
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            meta = doc.get("screening_meta") if isinstance(doc.get("screening_meta"), dict) else {}
            cands = doc.get("candidates_found")
            if cands is None:
                cands = meta.get("candidates_found")
            try:
                c = float(cands or 0)
            except (TypeError, ValueError):
                c = 0.0
            cand_vals.append(c)
            day = str(path.stem.replace("daily_signals_", ""))
            if len(day) == 8 and day.isdigit():
                day = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
            screens.append(
                {
                    "session_date_et": day,
                    "candidates_found": int(c),
                    "regime": meta.get("market_regime_at_screen") or doc.get("market_regime"),
                }
            )
            if latest_regime is None:
                latest_regime = meta.get("market_regime_at_screen") or doc.get("market_regime")
        if cand_vals:
            avg_candidates = round(sum(cand_vals) / len(cand_vals), 4)

    days_since_fill = None
    if last_fill_day:
        try:
            from utils.system_time import now

            today = now().date().isoformat()
            d0 = datetime.fromisoformat(last_fill_day).date()
            d1 = datetime.fromisoformat(today).date()
            days_since_fill = max(0, (d1 - d0).days)
        except Exception:
            days_since_fill = None

    days_since_entry = None
    has_open_position = False
    positions_path = data_dir / "positions.json" if data_dir else None
    if positions_path and positions_path.is_file():
        try:
            pos_doc = json.loads(positions_path.read_text(encoding="utf-8"))
            if isinstance(pos_doc, list) and pos_doc:
                has_open_position = True
                last_entry_day: str | None = None
                for pos in pos_doc:
                    if not isinstance(pos, dict):
                        continue
                    raw = str(pos.get("entry_date") or pos.get("timestamp") or "")[:10]
                    if len(raw) >= 10:
                        last_entry_day = raw
                if last_entry_day:
                    from utils.system_time import now

                    d0 = datetime.fromisoformat(last_entry_day).date()
                    d1 = now().date()
                    days_since_entry = max(0, (d1 - d0).days)
                elif has_open_position:
                    days_since_entry = 0
        except Exception:
            pass

    activity_vals = [d for d in (days_since_fill, days_since_entry) if d is not None]
    days_since_activity = min(activity_vals) if activity_vals else None

    return {
        "component": "classic_fortress",
        "window_sessions": window_sessions,
        "data_dir": str(data_dir) if data_dir else None,
        "ledger_path": str(ledger) if ledger else None,
        "sessions": [{"session_date_et": d, **by_day[d]} for d in fill_days],
        "screens": screens,
        "rolling_realized_usd": round(total_pnl, 4),
        "rolling_fills": total_fills,
        "rolling_exits": total_fills,
        "rolling_expectancy_usd": round(expectancy, 4) if expectancy is not None else None,
        "avg_candidates_per_screen": avg_candidates,
        "screens_sampled": len(screens),
        "days_since_last_fill": days_since_activity if days_since_activity is not None else days_since_fill,
        "days_since_last_exit": days_since_fill,
        "days_since_last_entry": days_since_entry,
        "days_since_last_activity": days_since_activity,
        "has_open_position": has_open_position,
        "latest_regime": latest_regime,
    }


def resolve_trading_bot_root() -> Path | None:
    tb = (os.environ.get("FORTRESS_TRADING_BOT_ROOT") or "").strip()
    if tb:
        p = Path(tb).expanduser()
        return p if p.is_dir() else None
    sibling = _repo_root().parent / "trading-bot"
    return sibling if sibling.is_dir() else None


def _load_trading_bot_queue_module(tb: Path):
    """Import trading-bot queue module from disk (avoid fortress-ai utils cache collision)."""
    import importlib.util

    path = tb / "utils" / "si_recommendation_queue.py"
    if not path.is_file():
        raise ImportError(f"trading_bot_queue_missing:{path}")
    spec = importlib.util.spec_from_file_location("trading_bot_si_recommendation_queue", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"trading_bot_queue_spec_failed:{path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def trigger_classic_si_cycle(*, assess_limit: int = 5, apply_limit: int = 2) -> dict[str, Any]:
    """Run trading-bot classic autonomous SI (assess queue + screener relax)."""
    import json
    import subprocess
    import sys

    tb = resolve_trading_bot_root()
    if not tb:
        return {"ok": False, "skipped": "trading_bot_missing"}
    py = tb / "venv" / "bin" / "python"
    if not py.is_file():
        py = Path(sys.executable)
    script = (
        "from utils.classic_si_autonomous import run_classic_si_cycle; "
        "import json; "
        f"print(json.dumps(run_classic_si_cycle(assess_limit={assess_limit}, apply_limit={apply_limit})))"
    )
    try:
        proc = subprocess.run(
            [str(py), "-c", script],
            cwd=str(tb),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            return {
                "ok": False,
                "error": (proc.stderr or proc.stdout or "classic_si_failed")[:200],
            }
        line = (proc.stdout or "").strip().splitlines()[-1]
        return json.loads(line) if line else {"ok": False, "error": "empty_output"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def push_findings_to_classic_queue(
    gaps: list[dict[str, Any]],
    recommendations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Upsert Classic objective gaps into trading-bot SI queue for autonomous apply."""
    tb = resolve_trading_bot_root()
    if not tb:
        return []
    try:
        tb_queue = _load_trading_bot_queue_module(tb)
        upsert_from_finding = tb_queue.upsert_from_finding
    except Exception:
        return []

    upserted: list[dict[str, Any]] = []
    metrics = classic_rolling_metrics(window_sessions=10)

    for gap in gaps:
        if str(gap.get("component") or "") != "classic_fortress":
            continue
        oid = str(gap.get("objective_id") or "")
        code = "classic_candidate_throughput"
        if oid in ("classic_fill_recency", "classic_fill_activity"):
            code = "classic_fill_recency"
        finding = {
            "code": code,
            "objective_id": oid,
            "severity": "high" if gap.get("priority") == "critical" else "medium",
            "component": "classic_fortress",
            "title": f"Classic objective gap: {oid}",
            "recommendation": (
                f"{oid}: {gap.get('metric')}={gap.get('value')} gap={gap.get('gap')}; "
                f"regime={metrics.get('latest_regime')}; days_since_fill={metrics.get('days_since_last_fill')}."
            ),
            "kind": "tunable",
            "effort": "low",
            "impact": gap.get("priority") or "high",
        }
        upserted.append(upsert_from_finding(finding, source="capability_review"))

    for rec in recommendations or []:
        if not isinstance(rec, dict):
            continue
        action = str(rec.get("action") or "")
        oid = str(rec.get("objective_id") or "")
        code = "classic_candidate_throughput"
        if oid in ("classic_fill_recency", "classic_fill_activity") or "fill" in action:
            code = "classic_fill_recency"
        elif action == "surpass_escalate" and oid:
            code = "classic_candidate_throughput" if "throughput" in oid else "classic_fill_recency" if "fill" in oid else "si_objective_gap"
        finding = {
            "code": code,
            "objective_id": oid or None,
            "severity": "medium",
            "component": "classic_fortress",
            "title": f"Classic SI: {action or code}",
            "recommendation": str(rec.get("detail") or ""),
            "kind": "tunable",
            "effort": "low",
            "impact": "high",
        }
        upserted.append(upsert_from_finding(finding, source="capability_review"))

    return upserted


def push_fortress_beliefs_to_classic_queue(*, limit: int = 5) -> list[dict[str, Any]]:
    """
    Export high-confidence Fortress AI beliefs into Classic SI queue for human review.
    Never auto-applied (source=fortress_ai_belief).
    """
    tb = resolve_trading_bot_root()
    if not tb:
        return []
    beliefs_path = _repo_root() / "data" / "beliefs" / "beliefs.json"
    if not beliefs_path.is_file():
        return []
    try:
        doc = json.loads(beliefs_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = doc if isinstance(doc, list) else doc.get("beliefs") if isinstance(doc, dict) else []
    if not isinstance(rows, list):
        return []
    ranked = sorted(
        [r for r in rows if isinstance(r, dict)],
        key=lambda r: float(r.get("confidence") or 0.0),
        reverse=True,
    )[: max(1, limit)]

    try:
        tb_queue = _load_trading_bot_queue_module(tb)
        upsert_from_finding = tb_queue.upsert_from_finding
    except Exception:
        return []

    upserted: list[dict[str, Any]] = []
    for row in ranked:
        regime = str(row.get("regime") or row.get("regime_label") or "any")
        strategy = str(row.get("strategy") or row.get("strategy_label") or "any")
        finding = {
            "code": "fortress_ai_belief_share",
            "severity": "medium",
            "component": "fortress_ai",
            "title": f"Fortress belief: {regime}/{strategy}",
            "recommendation": (
                f"Belief confidence={row.get('confidence')} lesson={str(row.get('lesson') or row.get('text') or '')[:400]}. "
                "Review for Classic applicability — does not auto-apply."
            ),
            "kind": "monitor",
            "effort": "low",
            "impact": "medium",
            "belief": row,
        }
        upserted.append(upsert_from_finding(finding, source="fortress_ai_belief"))
    return upserted


def push_classic_open_findings_to_fortress_queue(*, limit: int = 5) -> list[dict[str, Any]]:
    """Import open Classic SI items into fortress-ai queue for agent/human review (batch, not live sync)."""
    tb = resolve_trading_bot_root()
    if not tb:
        return []
    qpath = tb / "data" / "si_recommendation_queue.json"
    if not qpath.is_file():
        return []
    try:
        doc = json.loads(qpath.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = doc.get("items") if isinstance(doc, dict) else []
    if not isinstance(items, list):
        return []

    try:
        from utils.si_recommendation_queue import upsert_from_finding
    except Exception:
        return []

    upserted: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "open":
            continue
        if str(item.get("component") or "") not in ("classic_fortress", "fortress_ai"):
            continue
        finding = {
            "code": f"classic_mirror_{item.get('code')}",
            "severity": item.get("severity") or "medium",
            "component": "classic_fortress",
            "title": f"Classic queue mirror: {item.get('title') or item.get('code')}",
            "recommendation": str(item.get("recommendation") or "")[:2000],
            "kind": "monitor",
            "effort": "low",
            "impact": "medium",
            "classic_item_id": item.get("id"),
        }
        upserted.append(upsert_from_finding(finding, source="cross_stack_belief"))
        if len(upserted) >= limit:
            break
    return upserted
