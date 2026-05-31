"""Edge quality — payoff math, RR/cost/expectancy gates, time-stop."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from utils.edge_quality_config import (
    cost_gate_enabled,
    cost_gate_mult,
    est_fee_usd,
    est_slippage_usd,
    expectancy_gate_enabled,
    expectancy_min_exits,
    expectancy_min_usd,
    rr_gate_enabled,
    rr_safety_margin,
    time_stop_enabled,
    time_stop_min_progress_pct,
    time_stop_sec,
)


def breakeven_payoff(win_rate: float) -> float:
    """Minimum win/loss ratio needed at given WR."""
    wr = max(0.01, min(0.99, float(win_rate)))
    return (1.0 - wr) / wr


def payoff_ratio(avg_win: float, avg_loss: float) -> float | None:
    if avg_loss >= 0 or avg_win <= 0:
        return None
    return avg_win / abs(avg_loss)


def profit_factor(wins_pnl: float, losses_pnl: float) -> float | None:
    if losses_pnl >= 0:
        return None
    return wins_pnl / abs(losses_pnl)


def session_expectancy(sum_pnl: float, exits: int) -> float | None:
    if exits <= 0:
        return None
    return sum_pnl / exits


def round_trip_cost_usd(*, last: float, spread_bps: float | None) -> float:
    spread = 0.0
    if spread_bps is not None and last > 0:
        spread = last * float(spread_bps) / 10_000.0
    return round(spread + est_slippage_usd() * 2 + est_fee_usd() * 2, 4)


def rr_admission_ok(
    *,
    target_usd: float,
    stop_usd: float,
    win_rate: float | None = None,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Reward:risk must clear breakeven payoff for estimated WR."""
    if not rr_gate_enabled():
        return True, None, {}
    tgt = float(target_usd)
    stp = abs(float(stop_usd))
    if tgt <= 0 or stp <= 0:
        return False, "edge_rr_invalid", {"target_usd": tgt, "stop_usd": stp}
    rr = tgt / stp
    wr = float(win_rate) if win_rate is not None else 0.45
    need = breakeven_payoff(wr) * rr_safety_margin()
    ok = rr >= need
    meta = {
        "rr": round(rr, 3),
        "breakeven_rr": round(need, 3),
        "win_rate_used": round(wr, 3),
    }
    if not ok:
        return False, f"edge_rr_gate:rr={rr:.2f}<{need:.2f}", meta
    return True, None, meta


def cost_admission_ok(
    *,
    target_usd: float,
    last: float,
    spread_bps: float | None,
) -> tuple[bool, str | None, dict[str, Any]]:
    if not cost_gate_enabled():
        return True, None, {}
    cost = round_trip_cost_usd(last=last, spread_bps=spread_bps)
    need = cost * cost_gate_mult()
    ok = float(target_usd) >= need
    meta = {"round_trip_cost_usd": cost, "min_target_usd": round(need, 4)}
    if not ok:
        return False, f"edge_cost_gate:target={target_usd:.3f}<{need:.3f}", meta
    return True, None, meta


def pattern_expectancy(
    learned: dict[str, Any],
    pattern: str,
) -> dict[str, Any] | None:
    ps = (learned.get("pattern_stats") or {}).get(pattern)
    if not isinstance(ps, dict):
        return None
    exits = int(ps.get("exits") or 0)
    if exits < expectancy_min_exits():
        return None
    wins = int(ps.get("wins") or 0)
    losses = int(ps.get("losses") or 0)
    pnl = float(ps.get("sum_pnl_usd") or 0)
    wr = wins / exits if exits else None
    exp = pnl / exits if exits else None
    return {"exits": exits, "wins": wins, "losses": losses, "win_rate": wr, "expectancy_usd": exp}


def expectancy_admission_ok(
    *,
    symbol: str,
    pattern: str,
    component: str = "skim_swarm",
) -> tuple[bool, str | None, dict[str, Any]]:
    if not expectancy_gate_enabled():
        return True, None, {}
    try:
        if component == "infra_swarm":
            from agents.infra_swarm.symbol_learning import load_learned
        else:
            from agents.skim_swarm.symbol_learning import load_learned

        learned = load_learned(symbol)
    except Exception:
        return True, None, {}
    stats = pattern_expectancy(learned, pattern)
    if stats is None:
        return True, None, {"insufficient_data": True}
    exp = stats.get("expectancy_usd")
    if exp is not None and float(exp) < expectancy_min_usd():
        return (
            False,
            f"edge_expectancy_gate:{pattern}:exp={float(exp):.3f}",
            stats,
        )
    return True, None, stats


def evaluate_entry_edge_gates(
    *,
    symbol: str,
    pattern: str,
    side: str,
    features: dict[str, Any],
    target_usd: float,
    stop_usd: float,
    component: str = "skim_swarm",
) -> tuple[bool, str | None, dict[str, Any]]:
    """Combined RR + cost + expectancy admission."""
    meta: dict[str, Any] = {}
    last = float(features.get("last") or 0)
    spread = features.get("spread_bps")
    spread_f = float(spread) if spread is not None else None

    wr = None
    try:
        if component == "infra_swarm":
            from agents.infra_swarm.symbol_learning import load_learned
        else:
            from agents.skim_swarm.symbol_learning import load_learned

        ss = load_learned(symbol).get("session_stats") or {}
        exits = int(ss.get("exits") or 0)
        wins = int(ss.get("wins") or 0)
        if exits >= 3:
            wr = wins / exits
    except Exception:
        pass

    ok, reason, m = rr_admission_ok(target_usd=target_usd, stop_usd=stop_usd, win_rate=wr)
    meta["rr"] = m
    if not ok:
        return False, reason, meta

    ok, reason, m = cost_admission_ok(target_usd=target_usd, last=last, spread_bps=spread_f)
    meta["cost"] = m
    if not ok:
        return False, reason, meta

    ok, reason, m = expectancy_admission_ok(symbol=symbol, pattern=pattern, component=component)
    meta["expectancy"] = m
    if not ok:
        return False, reason, meta

    return True, None, meta


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def time_stop_triggered(
    symbol_state: dict[str, Any],
    *,
    unrealized: float,
    target_usd: float,
) -> bool:
    """Exit stale positions that haven't progressed toward target."""
    if not time_stop_enabled():
        return False
    entry_ts = _parse_ts(symbol_state.get("entry_ts"))
    if not entry_ts:
        return False
    age = (datetime.now(timezone.utc) - entry_ts).total_seconds()
    if age < time_stop_sec():
        return False
    tgt = float(target_usd)
    if tgt <= 0:
        return False
    progress = float(unrealized) / tgt
    return progress < time_stop_min_progress_pct()


def bracket_prices(
    *,
    side: str,
    entry_price: float,
    target_usd: float,
    stop_usd: float,
) -> tuple[float, float]:
    """Return (take_profit_price, stop_loss_price) for 1-share bracket."""
    side = side.lower()
    tgt = float(target_usd)
    stp = abs(float(stop_usd))
    ep = float(entry_price)
    if side == "long":
        return round(ep + tgt, 2), round(ep - stp, 2)
    return round(ep - tgt, 2), round(ep + stp, 2)
