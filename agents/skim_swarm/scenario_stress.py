"""Replay recent skim sessions from decisions.jsonl — stress-test param overlays."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agents.skim_swarm.pnl import _wave_session_date
from agents.skim_swarm.symbol_learning import _entry_pattern_from_reasoning
from utils.skim_swarm_config import normalize_symbol, swarm_data_dir, thin_etf_symbols, universe

_PATTERNS = ("rip_fade", "pullback_uptrend", "momentum_long", "momentum_short")


@dataclass
class TradeRecord:
    session_date: str
    symbol: str
    pattern: str | None
    side: str
    entry_score: float
    exit_pnl: float
    exit_reason: str
    target_usd: float
    base_target_mult: float = 1.0


@dataclass
class OverlayResult:
    overlay: dict[str, Any]
    sum_pnl_usd: float
    exits: int
    wins: int
    losses: int
    blocked: int

    @property
    def win_rate(self) -> float | None:
        closed = self.wins + self.losses
        return (self.wins / closed) if closed else None

    @property
    def expectancy(self) -> float | None:
        return (self.sum_pnl_usd / self.exits) if self.exits else None


def exit_reason_key(reasoning: str | None) -> str:
    raw = str(reasoning or "")
    if raw.startswith("skim_target_hit") or raw.startswith("skim_target_partial"):
        return "target_hit"
    if raw.startswith("stop_loss"):
        return "stop_loss"
    if raw.startswith("trailing_giveback"):
        return "trailing_giveback"
    if raw.startswith("eod_force_flatten"):
        return "eod_flatten"
    return "other"


def _base_enters(symbol: str) -> tuple[float, float]:
    thin = symbol in thin_etf_symbols()
    if thin:
        return 0.24, -0.24
    return 0.22, -0.22


def _entry_threshold(
    *,
    pattern: str | None,
    side: str,
    enter_long: float,
    enter_short: float,
    pattern_deltas: dict[str, float] | None = None,
) -> float | None:
    pd = pattern_deltas or {}
    if side == "long":
        if pattern == "pullback_uptrend":
            return enter_long + float(pd.get("pullback_uptrend") or 0)
        if pattern == "momentum_long":
            return enter_long + 0.12 + float(pd.get("momentum_long") or 0)
        return enter_long
    if side == "short":
        if pattern == "rip_fade":
            return enter_short + float(pd.get("rip_fade") or 0)
        if pattern == "momentum_short":
            return enter_short - 0.12 + float(pd.get("momentum_short") or 0)
        return enter_short
    return None


def entry_would_fire(trade: TradeRecord, overlay: dict[str, Any]) -> bool:
    if not trade.pattern:
        return True
    disabled = overlay.get("disable_patterns") or []
    if trade.pattern in disabled:
        return False
    el_base, es_base = _base_enters(trade.symbol)
    el = el_base + float(overlay.get("enter_long_delta") or 0)
    es = es_base + float(overlay.get("enter_short_delta") or 0)
    thr = _entry_threshold(
        pattern=trade.pattern,
        side=trade.side,
        enter_long=el,
        enter_short=es,
        pattern_deltas=overlay.get("pattern_deltas"),
    )
    if thr is None:
        return True
    if trade.side == "long":
        return trade.entry_score >= thr
    return trade.entry_score <= thr


def adjust_exit_pnl(
    trade: TradeRecord,
    overlay: dict[str, Any],
) -> float:
    pnl = float(trade.exit_pnl)
    base_tm = float(trade.base_target_mult or 1.0)
    new_tm = float(overlay.get("target_mult") or base_tm)
    if trade.exit_reason == "target_hit" and base_tm > 0 and new_tm != base_tm:
        return round(pnl * (new_tm / base_tm), 4)
    if trade.exit_reason == "stop_loss" and pnl < 0 and new_tm > base_tm:
        return round(pnl * 0.95, 4)
    return round(pnl, 4)


def score_overlay(trades: Iterable[TradeRecord], overlay: dict[str, Any]) -> OverlayResult:
    blocked = 0
    pnls: list[float] = []
    for t in trades:
        if not entry_would_fire(t, overlay):
            blocked += 1
            continue
        pnls.append(adjust_exit_pnl(t, overlay))
    wins = sum(1 for p in pnls if p >= 0)
    losses = len(pnls) - wins
    return OverlayResult(
        overlay=overlay,
        sum_pnl_usd=round(sum(pnls), 4),
        exits=len(pnls),
        wins=wins,
        losses=losses,
        blocked=blocked,
    )


def _default_overlay(**kwargs: Any) -> dict[str, Any]:
    base = {
        "enter_long_delta": 0.0,
        "enter_short_delta": 0.0,
        "target_mult": 1.0,
        "disable_patterns": [],
    }
    base.update(kwargs)
    return base


def candidate_overlays(symbol: str, trades: list[TradeRecord]) -> list[dict[str, Any]]:
    """Small grid: baseline, pattern disables from losers, tighter entries, target mult."""
    overlays: list[dict[str, Any]] = [_default_overlay()]
    pattern_pnl: dict[str, float] = {p: 0.0 for p in _PATTERNS}
    for t in trades:
        if t.pattern and t.pattern in pattern_pnl:
            pattern_pnl[t.pattern] += t.exit_pnl

    toxic = [p for p, pnl in pattern_pnl.items() if pnl < -0.5]
    if toxic:
        overlays.append(_default_overlay(disable_patterns=sorted(toxic)))

    for el_d, es_d in [(-0.04, 0.04), (-0.02, 0.02), (0.02, -0.02)]:
        overlays.append(_default_overlay(enter_long_delta=el_d, enter_short_delta=es_d))

    for tm in (0.85, 1.15):
        overlays.append(_default_overlay(target_mult=tm))

    if toxic:
        for el_d, es_d in [(-0.02, 0.02), (-0.04, 0.04)]:
            overlays.append(
                _default_overlay(
                    enter_long_delta=el_d,
                    enter_short_delta=es_d,
                    disable_patterns=sorted(toxic),
                )
            )

    # dedupe
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for o in overlays:
        key = json.dumps(o, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(o)
    return out


def load_trades_from_decisions(
    path: Path | None = None,
    *,
    max_sessions: int = 5,
) -> tuple[list[TradeRecord], list[str]]:
    """Pair executed entries with subsequent exits per symbol/session."""
    path = path or (swarm_data_dir() / "decisions.jsonl")
    if not path.exists():
        return [], []

    sessions_order: list[str] = []
    seen_sess: set[str] = set()
    pending: dict[tuple[str, str], dict[str, Any]] = {}
    trades: list[TradeRecord] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            wave = json.loads(line)
        except json.JSONDecodeError:
            continue
        sess = _wave_session_date(str(wave.get("ts") or ""))
        if not sess:
            continue
        if sess not in seen_sess:
            seen_sess.add(sess)
            sessions_order.append(sess)

        for row in wave.get("results") or []:
            sym = normalize_symbol(str(row.get("symbol") or ""))
            if not sym:
                continue
            act = row.get("act") or {}
            dec = row.get("decision") or {}
            if not act.get("executed"):
                continue
            action = str(dec.get("action") or "").lower()
            key = (sess, sym)

            if action in ("enter_long", "enter_short", "add_clip_long", "add_clip_short"):
                side = "long" if "long" in action else "short"
                pattern = _entry_pattern_from_reasoning(str(dec.get("reasoning") or ""))
                learned = row.get("learned_params") if isinstance(row.get("learned_params"), dict) else {}
                pending[key] = {
                    "side": side,
                    "pattern": pattern,
                    "entry_score": float(dec.get("score") or 0),
                    "target_mult": float(learned.get("target_mult") or 1.0),
                }
                continue

            if action not in ("exit_position", "exit_partial", "flatten"):
                continue

            ent = pending.pop(key, None)
            if not ent:
                continue
            features = row.get("features") if isinstance(row.get("features"), dict) else {}
            pnl_raw = features.get("unrealized_usd")
            if pnl_raw is None:
                continue
            pnl = float(pnl_raw)
            if action == "exit_partial":
                eq = max(1, int(dec.get("exit_qty") or 1))
                pq = max(1, int(features.get("position_qty") or eq))
                pnl = pnl / pq * eq

            trades.append(
                TradeRecord(
                    session_date=sess,
                    symbol=sym,
                    pattern=ent.get("pattern"),
                    side=str(ent.get("side") or "long"),
                    entry_score=float(ent.get("entry_score") or 0),
                    exit_pnl=pnl,
                    exit_reason=exit_reason_key(str(dec.get("reasoning") or "")),
                    target_usd=float(dec.get("target_usd") or 0),
                    base_target_mult=float(ent.get("target_mult") or 1.0),
                )
            )

    if max_sessions > 0 and len(sessions_order) > max_sessions:
        keep = set(sessions_order[-max_sessions:])
        trades = [t for t in trades if t.session_date in keep]
        sessions_order = sessions_order[-max_sessions:]

    return trades, sessions_order


def _rank_score(result: OverlayResult, *, baseline_exits: int) -> float:
    churn_pen = max(0, result.exits - baseline_exits) * 0.06
    return result.sum_pnl_usd - churn_pen


def stress_symbol(
    symbol: str,
    trades: list[TradeRecord],
    *,
    holdout_session: str | None = None,
) -> dict[str, Any]:
    sym_trades = [t for t in trades if t.symbol == symbol]
    if len(sym_trades) < 3:
        return {"symbol": symbol, "ok": False, "error": "insufficient_trades", "trades": len(sym_trades)}

    overlays = candidate_overlays(symbol, sym_trades)
    baseline = score_overlay(sym_trades, _default_overlay())
    best = baseline
    best_rank = _rank_score(baseline, baseline_exits=baseline.exits)

    for o in overlays[1:]:
        r = score_overlay(sym_trades, o)
        rank = _rank_score(r, baseline_exits=baseline.exits)
        if rank > best_rank + 0.01:
            best = r
            best_rank = rank

    holdout = holdout_session or sym_trades[-1].session_date
    train = [t for t in sym_trades if t.session_date != holdout]
    test = [t for t in sym_trades if t.session_date == holdout]

    train_base = score_overlay(train, _default_overlay()) if train else baseline
    test_base = score_overlay(test, _default_overlay()) if test else baseline
    train_best = score_overlay(train, best.overlay) if train else best
    test_best = score_overlay(test, best.overlay) if test else best

    improves_train = train_best.sum_pnl_usd >= train_base.sum_pnl_usd - 0.01
    improves_holdout = test_best.sum_pnl_usd >= test_base.sum_pnl_usd - 0.05
    reduces_churn = best.exits <= baseline.exits or best.sum_pnl_usd > baseline.sum_pnl_usd + 0.25
    apply_ok = (
        best.overlay != _default_overlay()
        and improves_train
        and (improves_holdout or best.sum_pnl_usd >= baseline.sum_pnl_usd + 0.35)
        and reduces_churn
    )

    return {
        "symbol": symbol,
        "ok": True,
        "trades": len(sym_trades),
        "sessions": sorted({t.session_date for t in sym_trades}),
        "baseline": {
            "sum_pnl_usd": baseline.sum_pnl_usd,
            "exits": baseline.exits,
            "win_rate": baseline.win_rate,
            "blocked": baseline.blocked,
        },
        "best": {
            "sum_pnl_usd": best.sum_pnl_usd,
            "exits": best.exits,
            "win_rate": best.win_rate,
            "blocked": best.blocked,
            "overlay": best.overlay,
        },
        "holdout_session": holdout,
        "holdout_baseline_pnl": test_base.sum_pnl_usd,
        "holdout_best_pnl": test_best.sum_pnl_usd,
        "apply_recommended": apply_ok,
        "recommended_params": best.overlay if apply_ok else None,
    }


def stress_universe(
    *,
    max_sessions: int = 5,
    decisions_path: Path | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    trades, sessions = load_trades_from_decisions(decisions_path, max_sessions=max_sessions)
    holdout = sessions[-1] if sessions else None
    symbols = sorted({t.symbol for t in trades} & set(universe()))
    results = [stress_symbol(sym, trades, holdout_session=holdout) for sym in symbols]

    report = {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": "decisions_jsonl_replay",
        "max_sessions": max_sessions,
        "sessions": sessions,
        "holdout_session": holdout,
        "trade_count": len(trades),
        "caveat": (
            "Counterfactual replay of recent executed entry/exit pairs from decisions.jsonl. "
            "Blocked entries remove trades; target_mult scales target-hit PnL. Not a full re-sim."
        ),
        "symbols": results,
    }
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def apply_scenario_stress_to_learned(report: dict[str, Any]) -> list[str]:
    """Merge winning scenario overlays into learned/*.json when apply_recommended."""
    from agents.skim_swarm.symbol_learning import load_learned, save_learned

    applied: list[str] = []
    for row in report.get("symbols") or []:
        if not row.get("ok") or not row.get("apply_recommended"):
            continue
        rec = row.get("recommended_params") or {}
        if not rec:
            continue
        sym = str(row["symbol"])
        learned = load_learned(sym)
        params = learned.setdefault("params", {})

        for k in ("enter_long_delta", "enter_short_delta", "target_mult"):
            if k in rec and rec[k] is not None:
                params[k] = rec[k]

        if "disable_patterns" in rec and rec["disable_patterns"] is not None:
            existing = list(params.get("disable_patterns") or [])
            merged = sorted(set(existing) | set(rec["disable_patterns"]))
            params["disable_patterns"] = merged

        learned["scenario_stress"] = {
            "ts": report.get("ts"),
            "sessions": row.get("sessions"),
            "baseline": row.get("baseline"),
            "best": row.get("best"),
            "holdout_session": row.get("holdout_session"),
            "recommended_params": rec,
        }
        notes = learned.setdefault("notes", [])
        notes.append(f"scenario_stress:apply:{row.get('holdout_session')}")
        learned["notes"] = notes[-12:]
        save_learned(sym, learned)
        applied.append(sym)
    return applied
