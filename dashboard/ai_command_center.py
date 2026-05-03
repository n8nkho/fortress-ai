#!/usr/bin/env python3
"""Fortress AI Command Center — futuristic dashboard + SSE + comparison APIs."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DASH = Path(__file__).resolve().parent
_ROOT = _DASH.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

try:
    from dotenv import load_dotenv

    _dotenv_override = str(os.environ.get("FORTRESS_DOTENV_OVERRIDE", "")).lower() in (
        "1",
        "true",
        "yes",
    )
    load_dotenv(_ROOT / ".env", override=_dotenv_override)
except Exception:
    pass

from flask import Flask, Response, jsonify, render_template, request

from utils.alpaca_env import is_alpaca_paper
from utils.api_costs import week_cost_usd
from utils.operator_halt import get_halt_state, set_trading_halt

_MACRO_CACHE: dict[str, Any] = {"t": 0.0, "spy": None, "vix": None}


def _macro_snapshot() -> dict[str, Any]:
    """Lightweight SPY/VIX for dashboard (45s cache)."""
    now = time.time()
    if now - float(_MACRO_CACHE["t"]) < 45 and _MACRO_CACHE.get("spy") is not None:
        return {"spy": _MACRO_CACHE["spy"], "vix": _MACRO_CACHE["vix"], "cached": True}
    try:
        import yfinance as yf

        spy = yf.Ticker("SPY")
        vix = yf.Ticker("^VIX")
        sp = spy.fast_info.get("last_price")
        if sp is None:
            sp = float(spy.history(period="5d", interval="1d")["Close"].iloc[-1])
        vx = vix.fast_info.get("last_price")
        if vx is None:
            vx = float(vix.history(period="5d", interval="1d")["Close"].iloc[-1])
        _MACRO_CACHE.update({"t": now, "spy": float(sp), "vix": float(vx)})
        return {"spy": float(sp), "vix": float(vx), "cached": False}
    except Exception as e:
        return {"spy": None, "vix": None, "error": str(e)[:80], "cached": False}


app = Flask(
    __name__,
    template_folder=_DASH / "templates",
    static_folder=_DASH / "static",
    static_url_path="/static",
)


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


def _infer_ui_status(last_metric: dict | None, last_decision_row: dict | None) -> str:
    if last_metric:
        if last_metric.get("executed"):
            return "EXECUTING"
        if last_metric.get("decision_latency_ms"):
            return "THINKING"
    if last_decision_row and last_decision_row.get("decision"):
        return "OBSERVING"
    return "WAITING"


def _alpaca_snapshot() -> dict[str, Any]:
    key = (os.environ.get("ALPACA_API_KEY") or "").strip()
    sec = (os.environ.get("ALPACA_SECRET_KEY") or "").strip()
    if not key or not sec:
        return {"connected": False, "reason": "missing_keys"}
    try:
        from alpaca.trading.client import TradingClient

        tc = TradingClient(key, sec, paper=is_alpaca_paper())
        acct = tc.get_account()
        pos = tc.get_all_positions()
        positions = []
        unreal = 0.0
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
        acct_num = getattr(acct, "account_number", None)
        acct_id = getattr(acct, "id", None)
        return {
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
    except Exception as e:
        return {"connected": False, "reason": f"{type(e).__name__}:{e}"}


def _comparison_payload() -> dict[str, Any]:
    ai_rows = _tail_jsonl(_data_dir() / "ai_metrics.jsonl", 500)
    classic_dir = _classic_data_dir()
    classic_win_rate = None
    classic_pnls: list[float] = []
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
            "dry_run": str(os.environ.get("FORTRESS_AI_DRY_RUN", "1")).lower() in ("1", "true", "yes"),
        },
        "note": (
            "Portfolio + Alpaca snapshot use ALPACA_* from this instance's environment. "
            "Classic columns read decisions_log.jsonl under CLASSIC_DATA_DIR only — "
            "they do not update when you rotate Alpaca keys unless that file changes."
        ),
    }


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

    loop_sec = float(os.environ.get("FORTRESS_AI_LOOP_SECONDS") or "300")

    status = _infer_ui_status(latest_metric, last_row)

    last_ts = None
    if latest_metric and latest_metric.get("ts"):
        last_ts = latest_metric["ts"]
    elif last_row and last_row.get("ts"):
        last_ts = last_row["ts"]

    portfolio = _alpaca_snapshot()

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
        "macro": _macro_snapshot(),
    }


@app.route("/")
def index():
    return render_template("ai_dashboard.html")


@app.route("/mockup")
def mockup():
    """Static layout preview with dummy data (design QA)."""
    return render_template("mockup.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "fortress-ai-dashboard"})


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


if __name__ == "__main__":
    port = int(os.environ.get("FORTRESS_AI_DASHBOARD_PORT") or os.environ.get("DASHBOARD_PORT") or "8084")
    app.run(host="0.0.0.0", port=port, debug=False)
