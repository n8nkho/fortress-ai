#!/usr/bin/env python3
"""Fortress AI Command Center — futuristic dashboard + SSE + comparison APIs."""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_DASH = Path(__file__).resolve().parent
_ROOT = _DASH.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

try:
    from utils.env_load import load_fortress_dotenv

    load_fortress_dotenv(_ROOT)
except Exception:
    pass

from flask import Flask, Response, jsonify, render_template, request

from utils.alpaca_env import (
    alpaca_credentials,
    alpaca_trading_client_kwargs,
    is_alpaca_paper,
)
from utils.api_costs import week_cost_usd
from utils.agent_runtime import (
    read_runtime_prefs,
    request_on_demand_cycle,
    write_runtime_prefs,
)
from utils.operator_halt import get_halt_state, set_trading_halt
from utils.us_equity_hours import effective_loop_interval_seconds, is_us_equity_rth_et

_MACRO_CACHE: dict[str, Any] = {"t": 0.0, "spy": None, "vix": None, "rsi": None}


def _rsi_from_close(close: Any, period: int = 14) -> float | None:
    """Classic RSI from closing prices (simple rolling mean of gains/losses)."""
    try:
        import pandas as pd

        if close is None or len(close) < period + 1:
            return None
        c = close.astype(float)
        delta = c.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_g = gain.rolling(period).mean()
        avg_l = loss.rolling(period).mean()
        rs = avg_g / avg_l.replace(0, 1e-12)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        v = rsi.iloc[-1]
        if pd.isna(v):
            return None
        return float(round(v, 2))
    except Exception:
        return None


def _macro_snapshot() -> dict[str, Any]:
    """Lightweight SPY/VIX + SPY RSI(14) for dashboard (45s cache)."""
    now = time.time()
    if now - float(_MACRO_CACHE["t"]) < 45 and _MACRO_CACHE.get("spy") is not None:
        return {
            "spy": _MACRO_CACHE["spy"],
            "vix": _MACRO_CACHE["vix"],
            "rsi": _MACRO_CACHE.get("rsi"),
            "cached": True,
        }
    try:
        import yfinance as yf

        spy = yf.Ticker("SPY")
        vix = yf.Ticker("^VIX")
        hist = spy.history(period="3mo", interval="1d")
        closes = hist["Close"] if hist is not None and len(hist.index) else None
        rsi_val = _rsi_from_close(closes, period=14)

        sp = spy.fast_info.get("last_price")
        if sp is None:
            sp = float(closes.iloc[-1]) if closes is not None and len(closes) else float(
                spy.history(period="5d", interval="1d")["Close"].iloc[-1]
            )
        vx = vix.fast_info.get("last_price")
        if vx is None:
            vx = float(vix.history(period="5d", interval="1d")["Close"].iloc[-1])
        _MACRO_CACHE.update(
            {"t": now, "spy": float(sp), "vix": float(vx), "rsi": rsi_val}
        )
        return {"spy": float(sp), "vix": float(vx), "rsi": rsi_val, "cached": False}
    except Exception as e:
        return {"spy": None, "vix": None, "rsi": None, "error": str(e)[:80], "cached": False}


app = Flask(
    __name__,
    template_folder=_DASH / "templates",
    static_folder=_DASH / "static",
    static_url_path="/static",
)

from dashboard.governance_panel import register_governance_routes  # noqa: E402

register_governance_routes(app)

# Shown in the UI so you can confirm which bundle is live. Override in .env if you want a custom label.
_DASHBOARD_UI_BUILD = (os.environ.get("FORTRESS_AI_DASHBOARD_BUILD") or "v2-2026-05-03-gov").strip()


@app.after_request
def _no_store_html_dashboard(response: Response):
    """Prevent proxies/browsers from caching HTML shell (stale dashboard after git pull)."""
    if request.method != "GET":
        return response
    path = request.path or ""
    if path in ("/", "/mockup"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def _classic_data_dir() -> Path | None:
    raw = (os.environ.get("CLASSIC_DATA_DIR") or "").strip()
    return Path(raw).expanduser() if raw else None


def _tail_jsonl(path: Path, max_lines: int = 80) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = path.read_bytes()
        if len(data) > 256_000:
            data = data[-256_000:]
        lines = data.decode("utf-8", errors="replace").strip().split("\n")
        rows = []
        for line in lines[-max_lines:]:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows
    except Exception:
        return []


def _today_llm_cost_usd() -> float:
    """Sum ledger entries for current UTC date."""
    p = _data_dir() / "ai_llm_cost_ledger.jsonl"
    if not p.exists():
        return 0.0
    today = datetime.now(timezone.utc).date().isoformat()
    total = 0.0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                    ts = str(o.get("timestamp") or "")
                    if ts.startswith(today):
                        total += float(o.get("cost_usd") or 0)
                except Exception:
                    continue
    except Exception:
        pass
    return round(total, 6)


def _calls_today_from_ledger() -> int:
    p = _data_dir() / "ai_llm_cost_ledger.jsonl"
    if not p.exists():
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    n = 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                    ts = str(o.get("timestamp") or "")
                    if ts.startswith(today):
                        n += 1
                except Exception:
                    continue
    except Exception:
        pass
    return n


def _infer_ui_status(
    last_metric: dict | None,
    last_decision_row: dict | None,
    *,
    manual_only: bool = False,
) -> str:
    # Manual-only: stale ai_latest_metric.json still has decision_latency_ms from the last run —
    # avoid showing perpetual THINKING; keep EXECUTING if last run actually submitted.
    if manual_only:
        if last_metric and last_metric.get("executed"):
            return "EXECUTING"
        if last_decision_row and last_decision_row.get("decision"):
            return "OBSERVING"
        return "WAITING"
    if last_metric:
        if last_metric.get("executed"):
            return "EXECUTING"
        if last_metric.get("decision_latency_ms"):
            return "THINKING"
    if last_decision_row and last_decision_row.get("decision"):
        return "OBSERVING"
    return "WAITING"


def _alpaca_snapshot() -> dict[str, Any]:
    key, sec = alpaca_credentials()
    if not key or not sec:
        return {"connected": False, "reason": "missing_keys"}
    try:
        from alpaca.trading.client import TradingClient

        tc = TradingClient(key, sec, **alpaca_trading_client_kwargs())
        acct = tc.get_account()
        acct_num = getattr(acct, "account_number", None)
        acct_id = getattr(acct, "id", None)
        positions: list[dict[str, Any]] = []
        unreal = 0.0
        positions_error: str | None = None
        try:
            pos = tc.get_all_positions()
            for p in pos[:40]:
                q = float(getattr(p, "qty", 0) or 0)
                mv = float(getattr(p, "market_value", 0) or 0)
                unreal += float(getattr(p, "unrealized_pl", 0) or 0)
                positions.append(
                    {
                        "symbol": getattr(p, "symbol", ""),
                        "qty": q,
                        "avg_entry": float(getattr(p, "avg_entry_price", 0) or 0),
                        "market_value": mv,
                        "unrealized_pl": float(getattr(p, "unrealized_pl", 0) or 0),
                        "current_price": float(getattr(p, "current_price", 0) or 0),
                    }
                )
        except Exception as pe:
            positions_error = f"{type(pe).__name__}:{pe}"[:240]

        out: dict[str, Any] = {
            "connected": True,
            "paper": is_alpaca_paper(),
            "account_number": acct_num,
            "alpaca_account_id": str(acct_id) if acct_id is not None else None,
            "api_key_tail": key[-4:] if len(key) >= 4 else None,
            "equity": float(acct.equity),
            "buying_power": float(acct.buying_power),
            "positions": positions,
            "unrealized_pl": round(unreal, 2),
            "position_count": len(positions),
        }
        if positions_error:
            out["positions_error"] = positions_error
        return out
    except Exception as e:
        return {"connected": False, "reason": f"{type(e).__name__}:{e}"}


def _symbols_from_ai_rows(rows: list[dict]) -> set[str]:
    syms: set[str] = set()
    for r in rows:
        d = r.get("decision")
        if not isinstance(d, dict):
            continue
        p = d.get("parameters")
        if isinstance(p, dict):
            s = p.get("symbol") or p.get("sym")
            if s:
                syms.add(str(s).strip().upper()[:12])
    return syms


def _walk_symbols(o: Any, syms: set[str], depth: int = 0) -> None:
    if depth > 10:
        return
    if isinstance(o, dict):
        for k, v in o.items():
            if str(k).lower() in ("ticker", "symbol", "sym") and isinstance(v, str):
                t = v.strip().upper()
                if 1 <= len(t) <= 12:
                    syms.add(t)
            else:
                _walk_symbols(v, syms, depth + 1)
    elif isinstance(o, list):
        for x in o[:80]:
            _walk_symbols(x, syms, depth + 1)


def _symbols_from_classic_jsonl(path: Path, max_lines: int = 400) -> set[str]:
    syms: set[str] = set()
    if not path.exists():
        return syms
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max_lines:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                _walk_symbols(json.loads(line), syms)
            except Exception:
                continue
    except Exception:
        pass
    return syms


def _comparison_payload() -> dict[str, Any]:
    dd = _data_dir()
    ai_rows = _tail_jsonl(dd / "ai_metrics.jsonl", 500)
    decisions_recent = _tail_jsonl(dd / "ai_decisions.jsonl", 220)
    classic_dir = _classic_data_dir()
    classic_win_rate = None
    classic_pnls: list[float] = []
    today_u = datetime.now(timezone.utc).date().isoformat()
    cycles_today = sum(1 for r in decisions_recent if str(r.get("ts") or "").startswith(today_u))
    executed_today = sum(
        1
        for r in decisions_recent
        if str(r.get("ts") or "").startswith(today_u) and (r.get("act") or {}).get("executed")
    )

    if classic_dir and classic_dir.is_dir():
        log_path = classic_dir / "decisions_log.jsonl"
        if log_path.exists():
            try:
                for line in log_path.open(encoding="utf-8"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                        p = o.get("pnl")
                        if p is not None:
                            classic_pnls.append(float(p))
                    except Exception:
                        continue
            except Exception:
                pass
    wins = sum(1 for p in classic_pnls if p > 0)
    if classic_pnls:
        classic_win_rate = round(wins / len(classic_pnls), 4)

    ai_non_wait = sum(1 for r in ai_rows if r.get("opportunity_detection"))
    ai_syms = _symbols_from_ai_rows(decisions_recent)
    classic_syms: set[str] = set()
    if classic_dir and classic_dir.is_dir():
        classic_syms = _symbols_from_classic_jsonl(classic_dir / "decisions_log.jsonl")
    overlap_syms = sorted(ai_syms & classic_syms)[:16]

    return {
        "classic": {
            "data_dir": str(classic_dir) if classic_dir else None,
            "closed_sample_trades": len(classic_pnls),
            "win_rate": classic_win_rate,
            "avg_pnl_per_trade": round(sum(classic_pnls) / len(classic_pnls), 4) if classic_pnls else None,
        },
        "fortress_ai": {
            "metrics_cycles": len(ai_rows),
            "opportunity_cycles": ai_non_wait,
            "cycles_today": cycles_today,
            "executed_today": executed_today,
            "dry_run": str(os.environ.get("FORTRESS_AI_DRY_RUN", "1")).lower() in ("1", "true", "yes"),
        },
        "overlap": {
            "symbols": overlap_syms,
            "ai_unique_symbols": len(ai_syms),
            "classic_unique_symbols": len(classic_syms),
        },
        "note": (
            "Portfolio + Alpaca snapshot use ALPACA_* from this instance's environment. "
            "Classic columns read decisions_log.jsonl under CLASSIC_DATA_DIR only — "
            "they do not update when you rotate Alpaca keys unless that file changes."
        ),
    }


def _chart_spy_intraday() -> dict[str, Any]:
    try:
        import yfinance as yf

        h = yf.Ticker("SPY").history(period="3d", interval="1h")
        if h is None or len(h.index) < 2:
            return {"labels": [], "prices": [], "change_pct": None}
        closes = h["Close"].astype(float)
        labels = [idx.strftime("%m/%d %H:%M") for idx in closes.index]
        prices = [float(x) for x in closes.tolist()]
        labels = labels[-40:]
        prices = prices[-40:]
        first, last = prices[0], prices[-1]
        chg = ((last - first) / first * 100) if first else None
        return {
            "labels": labels,
            "prices": prices,
            "change_pct": round(chg, 3) if chg is not None else None,
        }
    except Exception as e:
        return {"labels": [], "prices": [], "change_pct": None, "error": str(e)[:120]}


def _chart_llm_daily(days: int = 14) -> dict[str, Any]:
    dd = _data_dir()
    p = dd / "ai_llm_cost_ledger.jsonl"
    by_day: dict[str, float] = defaultdict(float)
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                        ts = str(o.get("timestamp") or "")[:10]
                        if len(ts) >= 10:
                            by_day[ts] += float(o.get("cost_usd") or 0)
                    except Exception:
                        continue
        except Exception:
            pass
    end = datetime.now(timezone.utc).date()
    labels: list[str] = []
    values: list[float] = []
    for i in range(days - 1, -1, -1):
        d = end - timedelta(days=i)
        ds = d.isoformat()
        labels.append(ds[5:])
        values.append(round(by_day.get(ds, 0.0), 6))
    return {"labels": labels, "ai_daily_usd": values}


def _tail_text_file(path: Path, max_lines: int = 50) -> str | None:
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return None


def _expert_bundle() -> dict[str, Any]:
    dd = _data_dir()
    ledger_tail: list[dict] = []
    lp = dd / "ai_llm_cost_ledger.jsonl"
    if lp.exists():
        try:
            for line in lp.read_text(encoding="utf-8").splitlines()[-30:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    ledger_tail.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            pass
    decisions = _tail_jsonl(dd / "ai_decisions.jsonl", 80)
    last_decision = decisions[-1] if decisions else None
    log_hint = (os.environ.get("FORTRESS_AI_DASHBOARD_LOG") or "").strip()
    log_path = Path(log_hint).expanduser() if log_hint else (_ROOT / "logs" / "fortress_ai.log")
    system_logs = _tail_text_file(log_path, 50)
    if system_logs is None:
        alt = dd / "agent_stdout.log"
        system_logs = _tail_text_file(alt, 50) or ""
    return {
        "cost_ledger_tail": ledger_tail[-20:],
        "last_decision": last_decision,
        "decisions_tail_count": len(decisions),
        "prompt_note": (
            "Full DeepSeek prompts are not persisted in data/ by default. "
            "Extend unified_ai_agent to log prompts, or inspect server logs."
        ),
        "system_logs": system_logs,
        "system_log_path": str(log_path) if log_path.exists() else None,
    }


def _screener_hint(decisions: list[dict]) -> dict[str, Any]:
    """Best-effort last screen_market watchlist from decision logs."""
    for r in reversed(decisions):
        d = r.get("decision")
        if not isinstance(d, dict):
            continue
        if (d.get("action") or "").lower() != "screen_market":
            continue
        p = d.get("parameters") if isinstance(d.get("parameters"), dict) else {}
        wl = p.get("watchlist") or p.get("symbols") or p.get("tickers")
        if isinstance(wl, list) and wl:
            return {"symbols": [str(x).upper()[:12] for x in wl[:12]], "ts": r.get("ts")}
        if isinstance(wl, str) and wl.strip():
            return {"symbols": [s.strip().upper() for s in wl.split(",") if s.strip()][:12], "ts": r.get("ts")}
    return {"symbols": [], "ts": None}


def build_current_state() -> dict[str, Any]:
    dd = _data_dir()
    latest_metric_path = dd / "ai_latest_metric.json"
    latest_metric = None
    if latest_metric_path.exists():
        try:
            latest_metric = json.loads(latest_metric_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    decisions = _tail_jsonl(dd / "ai_decisions.jsonl", 120)
    last_row = None
    for row in reversed(decisions):
        if row.get("decision") or row.get("error"):
            last_row = row
            break

    dec = (last_row or {}).get("decision") if last_row else None
    if isinstance(dec, dict):
        reasoning = dec.get("reasoning") or ""
        market_assessment = dec.get("market_assessment") or ""
        action = dec.get("action") or "wait"
        try:
            confidence = float(dec.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
    else:
        reasoning = ""
        market_assessment = ""
        action = "wait"
        confidence = 0.0

    state_path = dd / "ai_state.json"
    ai_state: dict = {}
    if state_path.exists():
        try:
            ai_state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    spent_week, wstart, wend = week_cost_usd()
    try:
        cap = float(os.environ.get("FORTRESS_AI_WEEKLY_COST_CAP_USD", "1.0"))
    except ValueError:
        cap = 1.0

    if latest_metric and latest_metric.get("next_sleep_sec") is not None:
        try:
            loop_sec = float(latest_metric["next_sleep_sec"])
        except (TypeError, ValueError):
            loop_sec = effective_loop_interval_seconds()
    else:
        loop_sec = effective_loop_interval_seconds()

    _manual_only_ui = str(os.environ.get("FORTRESS_AI_MANUAL_ONLY", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
    )
    status = _infer_ui_status(latest_metric, last_row, manual_only=_manual_only_ui)

    last_ts = None
    if latest_metric and latest_metric.get("ts"):
        last_ts = latest_metric["ts"]
    elif last_row and last_row.get("ts"):
        last_ts = last_row["ts"]

    portfolio = _alpaca_snapshot()
    ar = read_runtime_prefs()
    macro = _macro_snapshot()
    try:
        from knowledge.intel import domain_intel_snapshot

        domain_intel = domain_intel_snapshot(macro, beliefs=ai_state.get("beliefs") or {})
    except Exception as e:
        domain_intel = {"enabled": False, "error": str(e)[:120]}

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "instance": os.environ.get("FORTRESS_INSTANCE_NAME", "Fortress-AI"),
        "ui_status": status,
        "dry_run": str(os.environ.get("FORTRESS_AI_DRY_RUN", "1")).lower() in ("1", "true", "yes"),
        "min_confidence": float(os.environ.get("FORTRESS_AI_MIN_CONFIDENCE", "0.8") or 0.8),
        "weekly_cost_cap_usd": cap,
        "weekly_llm_spend_usd": spent_week,
        "week_window_utc": {"start": wstart.isoformat(), "end": wend.isoformat()},
        "today_llm_spend_usd": _today_llm_cost_usd(),
        "llm_calls_today": _calls_today_from_ledger(),
        "loop_interval_seconds": loop_sec,
        "us_equity_rth": is_us_equity_rth_et(),
        "last_decision_ts": last_ts,
        "reasoning": reasoning,
        "market_assessment": market_assessment,
        "action": action,
        "confidence": confidence,
        "beliefs": ai_state.get("beliefs") or {},
        "last_actions": (ai_state.get("last_actions") or [])[-12:],
        "latest_metric": latest_metric,
        "recent_decisions": decisions[-15:],
        "portfolio": portfolio,
        "halt": get_halt_state(),
        "macro": macro,
        "domain_intel": domain_intel,
        "agent_runtime": {
            "run_off_hours_auto": bool(ar.get("run_off_hours_auto", False)),
            "updated_at_utc": ar.get("updated_at_utc"),
        },
        "agent_schedule": {
            "manual_only": str(os.environ.get("FORTRESS_AI_MANUAL_ONLY", "0")).strip().lower()
            in ("1", "true", "yes"),
            "rth": is_us_equity_rth_et(),
        },
        "screener": _screener_hint(decisions),
        "learning": {
            "decisions_logged": len(decisions),
            "beliefs_keys": len((ai_state.get("beliefs") or {}).keys()),
        },
    }


@app.route("/")
def index():
    return render_template("ai_dashboard.html", ui_build=_DASHBOARD_UI_BUILD)


@app.route("/mockup")
def mockup():
    """Static layout preview with dummy data (design QA)."""
    return render_template("mockup.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "fortress-ai-dashboard"})


@app.route("/api/alpaca/diagnose")
def api_alpaca_diagnose():
    """Safe Alpaca connectivity check (no secrets). Use when the UI shows offline."""
    from urllib.parse import urlparse

    key, sec = alpaca_credentials()
    kw = alpaca_trading_client_kwargs()
    base = (os.getenv("ALPACA_BASE_URL") or "").strip()
    host = urlparse(base).netloc if base else ""
    out: dict[str, Any] = {
        "sdk_installed": False,
        "keys_configured": bool(key and sec),
        "api_key_tail": key[-4:] if len(key) >= 4 else None,
        "paper_mode": is_alpaca_paper(),
        "base_url_host": host or None,
        "client_uses_url_override": bool(kw.get("url_override")),
        "connected": False,
        "reason": None,
    }
    try:
        from alpaca.trading.client import TradingClient

        out["sdk_installed"] = True
    except ImportError:
        out["reason"] = "alpaca_sdk_missing"
        return jsonify(out)

    if not key or not sec:
        out["reason"] = "missing_keys"
        return jsonify(out)

    try:
        tc = TradingClient(key, sec, **kw)
        acct = tc.get_account()
        out["connected"] = True
        out["account_status"] = str(getattr(acct, "status", "") or "")
        eq = getattr(acct, "equity", None)
        out["equity"] = float(eq) if eq is not None else None
        try:
            _ = tc.get_all_positions()
            out["positions_fetch"] = "ok"
        except Exception as pe:
            out["positions_fetch"] = "error"
            out["positions_fetch_reason"] = f"{type(pe).__name__}:{pe}"[:240]
    except Exception as e:
        out["reason"] = f"{type(e).__name__}:{e}"[:300]

    return jsonify(out)


@app.route("/api/build")
def api_build():
    """Verify deployed revision from the browser (no dashboard HTML cache involved)."""
    git_short = None
    try:
        import subprocess

        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            git_short = (proc.stdout or "").strip() or None
    except Exception:
        pass
    dash_html = _DASH / "templates" / "ai_dashboard.html"
    has_ops_hint = False
    try:
        has_ops_hint = "fai-ops-panels-hint" in dash_html.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return jsonify(
        {
            "ui_build": _DASHBOARD_UI_BUILD,
            "git_short": git_short,
            "default_ui_build_in_code": "v2-2026-05-03-gov",
            "template_has_ops_panels_hint": has_ops_hint,
            "repo_root": str(_ROOT),
        }
    )


@app.route("/api/fortress_ai/status")
def ai_status():
    """Legacy aggregate — prefer /api/ai/current_state."""
    cs = build_current_state()
    return jsonify(
        {
            "instance": cs["instance"],
            "latest_metric": cs.get("latest_metric"),
            "state_preview": {"beliefs": cs.get("beliefs"), "last_actions": cs.get("last_actions")},
            "dry_run": cs["dry_run"],
            "min_confidence": cs["min_confidence"],
            "weekly_cost_cap_usd": cs["weekly_cost_cap_usd"],
        }
    )


@app.route("/api/ai/current_state")
def api_current_state():
    return jsonify(build_current_state())


@app.route("/api/comparison")
def api_comparison():
    return jsonify(_comparison_payload())


@app.route("/api/export/bundle")
def api_export_bundle():
    bundle = {
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "current_state": build_current_state(),
        "comparison": _comparison_payload(),
    }
    return Response(
        json.dumps(bundle, indent=2, default=str),
        mimetype="application/json",
        headers={
            "Content-Disposition": "attachment; filename=fortress_ai_export.json",
        },
    )


@app.route("/api/stream/decisions")
def stream_decisions():
    """SSE — pushes snapshot every ~3s (last metric + halt)."""

    def generate():
        last_sent = ""
        while True:
            try:
                snap = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "state": build_current_state(),
                }
                blob = json.dumps(snap, default=str)
                if blob != last_sent:
                    yield f"data: {blob}\n\n"
                    last_sent = blob
                else:
                    yield f": heartbeat {time.time()}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            time.sleep(3)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/operator/halt", methods=["GET"])
def api_halt_get():
    return jsonify(get_halt_state())


@app.route("/api/operator/halt", methods=["POST"])
def api_halt_post():
    active = bool(request.json.get("active")) if request.is_json else request.form.get("active") == "1"
    reason = (request.json.get("reason") if request.is_json else request.form.get("reason")) or ""
    actor = (request.json.get("actor") if request.is_json else request.form.get("actor")) or "dashboard"
    st = set_trading_halt(active, reason=str(reason), actor=str(actor))
    return jsonify({"ok": True, "state": get_halt_state(), "file": st})


@app.route("/api/agent/runtime", methods=["GET"])
def api_agent_runtime_get():
    return jsonify(read_runtime_prefs())


@app.route("/api/agent/runtime", methods=["POST"])
def api_agent_runtime_post():
    if not request.is_json:
        return jsonify({"ok": False, "error": "application/json required"}), 400
    body = request.get_json(silent=True) or {}
    if "run_off_hours_auto" not in body:
        return jsonify({"ok": False, "error": "run_off_hours_auto required"}), 400
    rec = write_runtime_prefs(run_off_hours_auto=bool(body["run_off_hours_auto"]))
    return jsonify({"ok": True, **rec})


@app.route("/api/agent/run-cycle", methods=["POST"])
def api_agent_run_cycle():
    request_on_demand_cycle()
    return jsonify({"ok": True})


@app.route("/api/charts/dashboard")
def api_charts_dashboard():
    return jsonify(
        {
            "spy": _chart_spy_intraday(),
            "llm_cost": _chart_llm_daily(14),
        }
    )


@app.route("/api/expert/bundle")
def api_expert_bundle():
    return jsonify(_expert_bundle())


@app.route("/api/self_improvement/status")
def api_self_improvement_status():
    from agents.self_improvement_engine import get_engine

    return jsonify(get_engine().status_dict())


@app.route("/api/self_improvement/proposals")
def api_self_improvement_proposals():
    from agents.self_improvement_engine import get_engine

    eng = get_engine()
    return jsonify(
        {
            "proposals": eng.list_recent_log(120),
            "total_lines": eng.count_log_lines(),
        }
    )


@app.route("/api/self_improvement/propose", methods=["POST"])
def api_self_improvement_propose():
    from agents.self_improvement_engine import get_engine

    try:
        rec = get_engine().analyze_performance_and_propose_change()
        return jsonify({"ok": True, "record": rec})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/self_improvement/approve", methods=["POST"])
def api_self_improvement_approve():
    from agents.self_improvement_engine import get_engine

    body = request.get_json(silent=True) or {}
    pid = body.get("proposal_id")
    try:
        rec = get_engine().approve_pending(proposal_id=pid)
        return jsonify({"ok": True, "record": rec})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/self_improvement/reject", methods=["POST"])
def api_self_improvement_reject():
    from agents.self_improvement_engine import get_engine

    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason") or "")
    try:
        rec = get_engine().reject_pending(reason=reason)
        return jsonify({"ok": True, "record": rec})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/self_improvement/revert", methods=["POST"])
def api_self_improvement_revert():
    from agents.self_improvement_engine import get_engine

    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason") or "dashboard_revert")
    try:
        rec = get_engine().revert_last_overrides(reason=reason)
        return jsonify({"ok": True, "record": rec})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/self_improvement/monitor", methods=["POST"])
def api_self_improvement_monitor():
    from agents.self_improvement_engine import get_engine

    r = get_engine().monitor_and_revert_if_needed()
    return jsonify({"ok": True, "action": r})


@app.route("/api/prompt_evolution/status")
def api_prompt_evolution_status():
    from agents.prompt_evolution import get_prompt_evolution

    return jsonify(get_prompt_evolution().status_dict())


@app.route("/api/prompt_evolution/log")
def api_prompt_evolution_log():
    from agents.prompt_evolution import get_prompt_evolution

    return jsonify({"entries": get_prompt_evolution().list_recent_log(100)})


@app.route("/api/prompt_evolution/analyze", methods=["POST"])
def api_prompt_evolution_analyze():
    from agents.prompt_evolution import get_prompt_evolution

    try:
        d = get_prompt_evolution().analyze_prompt_effectiveness()
        return jsonify({"ok": True, "analysis": d})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/prompt_evolution/propose", methods=["POST"])
def api_prompt_evolution_propose():
    from agents.prompt_evolution import get_prompt_evolution

    try:
        p = get_prompt_evolution().propose_prompt_improvement()
        return jsonify({"ok": True, "pending": p})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/prompt_evolution/approve", methods=["POST"])
def api_prompt_evolution_approve():
    from agents.prompt_evolution import get_prompt_evolution

    body = request.get_json(silent=True) or {}
    pid = body.get("proposal_id")
    try:
        rec = get_prompt_evolution().approve_pending(proposal_id=pid)
        return jsonify({"ok": True, "record": rec})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/prompt_evolution/reject", methods=["POST"])
def api_prompt_evolution_reject():
    from agents.prompt_evolution import get_prompt_evolution

    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason") or "")
    try:
        rec = get_prompt_evolution().reject_pending(reason=reason)
        return jsonify({"ok": True, "record": rec})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/prompt_evolution/revert", methods=["POST"])
def api_prompt_evolution_revert():
    from agents.prompt_evolution import get_prompt_evolution

    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason") or "dashboard_revert")
    try:
        rec = get_prompt_evolution().revert_overlay(reason=reason)
        return jsonify({"ok": True, "record": rec})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/prompt_evolution/ab/start", methods=["POST"])
def api_prompt_evolution_ab_start():
    from agents.prompt_evolution import get_prompt_evolution

    body = request.get_json(silent=True) or {}
    try:
        days = int(body.get("duration_days") or 7)
    except (TypeError, ValueError):
        days = 7
    try:
        rec = get_prompt_evolution().start_ab_test(duration_days=days)
        return jsonify({"ok": True, "record": rec})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/prompt_evolution/ab/end", methods=["POST"])
def api_prompt_evolution_ab_end():
    from agents.prompt_evolution import get_prompt_evolution

    body = request.get_json(silent=True) or {}
    winner = str(body.get("winner") or "").strip()
    reason = str(body.get("reason") or "")
    try:
        rec = get_prompt_evolution().end_ab_test(winner=winner, reason=reason)
        return jsonify({"ok": True, "record": rec})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


if __name__ == "__main__":
    port = int(os.environ.get("FORTRESS_AI_DASHBOARD_PORT") or os.environ.get("DASHBOARD_PORT") or "8084")
    app.run(host="0.0.0.0", port=port, debug=False)
