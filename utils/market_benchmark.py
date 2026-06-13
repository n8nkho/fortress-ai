"""Session market benchmark vs portfolio — feeds SI objective gaps and integrity scan."""
from __future__ import annotations

import os
from typing import Any

DEFAULT_BENCHMARK = "SPY"
STRONG_TAPE_1D_PCT = 0.35  # ~35bp+ daily move counts as constructive tape


def _enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_MARKET_BENCHMARK", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _benchmark_symbol() -> str:
    return (os.environ.get("FORTRESS_SI_BENCHMARK_SYMBOL") or DEFAULT_BENCHMARK).strip().upper()


def _reference_equity_usd() -> float:
    try:
        return max(1000.0, float(os.environ.get("FORTRESS_SI_BENCHMARK_EQUITY_USD", "100000") or 100000))
    except ValueError:
        return 100_000.0


def _pct_change(last: float, prior: float) -> float | None:
    if prior == 0:
        return None
    return round((last / prior - 1.0) * 100.0, 4)


def _tape_trend_label(change_5d_pct: float | None, price_vs_sma20_pct: float | None) -> str:
    if change_5d_pct is not None and change_5d_pct >= 1.0:
        return "uptrend"
    if change_5d_pct is not None and change_5d_pct <= -1.0:
        return "downtrend"
    if price_vs_sma20_pct is not None and price_vs_sma20_pct >= 0.5:
        return "uptrend"
    if price_vs_sma20_pct is not None and price_vs_sma20_pct <= -0.5:
        return "downtrend"
    return "mixed"


def fetch_benchmark_context(*, symbol: str | None = None) -> dict[str, Any]:
    """Delayed daily benchmark (default SPY) for SI comparison — not live execution."""
    sym = (symbol or _benchmark_symbol()).strip().upper()
    out: dict[str, Any] = {
        "ok": False,
        "benchmark": sym,
        "as_of": None,
        "benchmark_last": None,
        "change_1d_pct": None,
        "change_5d_pct": None,
        "tape_trend": None,
        "strong_tape_1d": False,
        "error": None,
        "source": "yfinance_delayed",
        "disabled": False,
    }
    if not _enabled():
        out["disabled"] = True
        out["error"] = "FORTRESS_SI_MARKET_BENCHMARK disabled"
        return out
    try:
        import yfinance as yf
    except ImportError:
        out["error"] = "yfinance_not_installed"
        return out
    try:
        hist = yf.download(
            sym,
            period="1mo",
            interval="1d",
            progress=False,
            auto_adjust=True,
            threads=False,
        )
        if hist is None or hist.empty:
            out["error"] = "benchmark_no_data"
            return out
        close = hist["Close"]
        if hasattr(close, "columns"):
            close = close[sym] if sym in close.columns else close.iloc[:, 0]
        closes = close.astype(float).dropna()
        if closes.empty:
            out["error"] = "benchmark_no_data"
            return out
        last = float(closes.iloc[-1])
        out["benchmark_last"] = round(last, 4)
        out["as_of"] = str(closes.index[-1])[:10]
        if len(closes) >= 2:
            out["change_1d_pct"] = _pct_change(last, float(closes.iloc[-2]))
        if len(closes) >= 6:
            out["change_5d_pct"] = _pct_change(last, float(closes.iloc[-6]))
        sma20 = float(closes.tail(20).mean()) if len(closes) >= 20 else None
        vs_sma = _pct_change(last, sma20) if sma20 else None
        out["tape_trend"] = _tape_trend_label(out.get("change_5d_pct"), vs_sma)
        c1 = out.get("change_1d_pct")
        out["strong_tape_1d"] = c1 is not None and float(c1) >= STRONG_TAPE_1D_PCT
        out["ok"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}:{e}"
    return out


def _session_combined_realized_usd() -> tuple[float, int]:
    """Today's realized net across skim + infra swarms."""
    from agents.infra_swarm.pnl import compute_pnl_summary as infra_pnl
    from agents.skim_swarm.pnl import compute_pnl_summary as skim_pnl

    sk = skim_pnl()
    inf = infra_pnl()
    net = float(sk["daily"]["net_usd"]) + float(inf["daily"]["net_usd"])
    exits = int(sk["daily"]["exit_count"]) + int(inf["daily"]["exit_count"])
    return round(net, 4), exits


def build_portfolio_session_metrics(
    *,
    benchmark: dict[str, Any] | None = None,
    reference_equity_usd: float | None = None,
) -> dict[str, Any]:
    """
    Compare session realized PnL to benchmark move.

    alpha_vs_spy_pct: portfolio session return % minus benchmark 1d change %.
    Negative => underperforming the tape on a comparable buy-and-hold day.
    """
    bench = benchmark if benchmark is not None else fetch_benchmark_context()
    eq = float(reference_equity_usd if reference_equity_usd is not None else _reference_equity_usd())
    net_usd, exit_count = _session_combined_realized_usd()
    session_return_pct = round((net_usd / eq) * 100.0, 4) if eq > 0 else 0.0
    spy_1d = bench.get("change_1d_pct")
    alpha = None
    if bench.get("ok") and spy_1d is not None:
        alpha = round(session_return_pct - float(spy_1d), 4)
    min_participation = 6
    try:
        from utils.si_capability_review import get_capability

        min_participation = int(get_capability("strong_tape_min_exits", 6) or 6)
    except Exception:
        pass
    participation_shortfall = 0
    if bench.get("strong_tape_1d") and exit_count < min_participation:
        participation_shortfall = min_participation - exit_count
    return {
        "component": "portfolio_session",
        "reference_equity_usd": eq,
        "session_realized_usd": net_usd,
        "session_return_pct": session_return_pct,
        "session_exit_count": exit_count,
        "benchmark_symbol": bench.get("benchmark"),
        "benchmark_change_1d_pct": spy_1d,
        "benchmark_change_5d_pct": bench.get("change_5d_pct"),
        "benchmark_tape_trend": bench.get("tape_trend"),
        "strong_tape_1d": bool(bench.get("strong_tape_1d")),
        "alpha_vs_spy_pct": alpha,
        "participation_shortfall_exits": participation_shortfall,
        "rolling_exits": exit_count,
        "benchmark_ok": bool(bench.get("ok")),
        "benchmark_error": bench.get("error"),
    }


def market_relative_findings(
    portfolio: dict[str, Any] | None = None,
    *,
    benchmark: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Integrity-style findings when tape and portfolio diverge."""
    bench = benchmark if benchmark is not None else fetch_benchmark_context()
    port = portfolio if portfolio is not None else build_portfolio_session_metrics(benchmark=bench)
    findings: list[dict[str, Any]] = []
    if not bench.get("ok"):
        return findings

    alpha = port.get("alpha_vs_spy_pct")
    spy_1d = bench.get("change_1d_pct")
    exits = int(port.get("session_exit_count") or 0)
    net = float(port.get("session_realized_usd") or 0)
    trend = bench.get("tape_trend")
    sym = bench.get("benchmark") or "SPY"

    if bench.get("strong_tape_1d") and alpha is not None and float(alpha) < -0.25:
        findings.append(
            {
                "severity": "high",
                "code": "market_relative_underperformance",
                "component": "portfolio_session",
                "si_action": "capability_review",
                "recommendation": (
                    f"Session underperformed {sym} ({spy_1d}% 1d) by {abs(float(alpha)):.2f}pp "
                    f"alpha with {exits} exits and ${net:+.2f} realized — review entry blocks "
                    f"(denylist, pause_entries, pattern disables) on constructive tape."
                ),
                "detail": {
                    "alpha_vs_spy_pct": alpha,
                    "benchmark_change_1d_pct": spy_1d,
                    "session_realized_usd": net,
                    "session_exit_count": exits,
                },
            }
        )

    if bench.get("strong_tape_1d") and exits < int(port.get("participation_shortfall_exits") or 0) + exits:
        shortfall = int(port.get("participation_shortfall_exits") or 0)
        if shortfall > 0:
            findings.append(
                {
                    "severity": "medium",
                    "code": "market_participation_gap",
                    "component": "portfolio_session",
                    "si_action": "capability_review",
                    "recommendation": (
                        f"Constructive tape ({sym} {spy_1d}% 1d, trend={trend}) but only {exits} "
                        f"swarm exits — participation shortfall {shortfall}; SI should not over-tighten "
                        f"on strong market days."
                    ),
                    "detail": {
                        "participation_shortfall_exits": shortfall,
                        "benchmark_change_1d_pct": spy_1d,
                    },
                }
            )

    if trend == "uptrend" and net <= 0 and exits >= 3 and alpha is not None and float(alpha) < 0:
        findings.append(
            {
                "severity": "medium",
                "code": "negative_alpha_active_session",
                "component": "portfolio_session",
                "si_action": "edge_autofix",
                "recommendation": (
                    f"Active session ({exits} exits) but negative alpha vs {sym} on {trend} tape — "
                    "review symbol selection and pattern disables vs beta leaders."
                ),
                "detail": {"alpha_vs_spy_pct": alpha, "session_exit_count": exits},
            }
        )
    return findings
