"""Convert mined patterns + regime knowledge into belief JSON records."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from utils.llm_resilience import exponential_backoff_retry

from agents.historical_seeder.seed_tiers import clamp_conf_for_tier, resolve_seed_tier, tier_label

logger = logging.getLogger("historical_seeder.belief_seeder")

DATE_RANGE = "2000-2026"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cap_legacy_meta_conf(x: float) -> float:
    """Playbooks / regime META rows (tier 2 narrative)."""
    return clamp_conf_for_tier("2", float(x))


def _lesson_llm(
    *,
    strategy: str,
    total_occurrences: int,
    win_rate: float,
    avg_return_5d: float,
    best_regime: str,
) -> str:
    prompt = (
        "In one specific, actionable sentence, what does this historical pattern teach about "
        f"{strategy} trading? Data: {total_occurrences} occurrences from 2000-2026, "
        f"win rate {win_rate:.1%}, avg 5-day return {avg_return_5d:+.2f}%, "
        f"best in {best_regime} regime. Be precise and quantitative."
    )

    @exponential_backoff_retry(max_retries=2)
    def _call():
        from agents.unified_ai_agent import call_deepseek

        text, _ = call_deepseek(prompt, max_out_tokens=120)
        return (text or "").strip()

    try:
        s = _call()
        return s[:800] if s else ""
    except Exception:
        logger.exception("belief seed lesson LLM failed")
        return ""


def _fallback_lesson(
    strategy: str,
    total_occurrences: int,
    win_rate: float,
    avg_return_5d: float,
    best_regime: str,
) -> str:
    return (
        f"{strategy} setup: {win_rate:.0%} win rate over {total_occurrences} "
        f"occurrences (2000-2026), avg {avg_return_5d:+.2f}% over 5 days. "
        f"Works best in {best_regime} regime."
    )


def patterns_to_belief_rows(pattern_results: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Returns (records, rejection_reasons)."""
    rows: list[dict[str, Any]] = []
    rejected: list[str] = []
    pats = pattern_results.get("patterns") or {}

    def pack_row(
        *,
        symbol: str,
        regime: str,
        strategy_used: str,
        entry_conf: float,
        outcome: str,
        pnl_pct: float,
        hold_h: float,
        lesson: str,
        conf_score: float,
        wins: int,
        losses: int,
        regimes_tested: list[str],
        seed_tier: str,
    ) -> dict[str, Any]:
        cf = clamp_conf_for_tier(seed_tier, conf_score)
        ef = clamp_conf_for_tier(seed_tier, entry_conf)
        desc = lesson.strip()
        if seed_tier != "1":
            suffix = f" [seed tier {seed_tier}: {tier_label(seed_tier)}]"
            room = max(0, 800 - len(suffix))
            desc = (desc[:room] + suffix)[:800]
        else:
            desc = desc[:800]
        return {
            "belief_id": str(uuid.uuid4()),
            "created_at": _now(),
            "symbol": symbol.upper(),
            "regime_at_entry": regime,
            "strategy_used": strategy_used,
            "entry_signal_confidence": ef,
            "outcome": outcome,
            "pnl_pct": float(pnl_pct),
            "hold_duration_hours": float(hold_h),
            "pattern_description": desc,
            "confidence_score": cf,
            "confirmation_count": int(wins),
            "refutation_count": int(losses),
            "last_updated_at": _now(),
            "source": "historical_seed",
            "seed_tier": str(seed_tier),
            "sample_size": wins + losses,
            "date_range": DATE_RANGE,
            "regimes_tested": regimes_tested,
        }

    # mean_reversion_rsi_extremes
    mr = pats.get("mean_reversion_rsi_extremes") or {}
    n = int(mr.get("total_occurrences") or 0)
    wr = float(mr.get("win_rate") or 0)
    cf = float(mr.get("confidence_score") or 0)
    tier_m = resolve_seed_tier(n=n, win_rate=wr, laplace_conf=cf)
    if tier_m:
        avg5 = float(mr.get("avg_return_5d") or 0)
        br = str(mr.get("best_regime") or "UNKNOWN")
        strat = "mean_reversion_rsi"
        les = _lesson_llm(
            strategy=strat,
            total_occurrences=n,
            win_rate=wr,
            avg_return_5d=avg5,
            best_regime=br,
        ) or _fallback_lesson(strat, n, wr, avg5, br)
        wc = int(mr.get("win_count") or 0)
        lc = int(mr.get("loss_count") or 0)
        rt = list((mr.get("by_regime_win_rate") or {}).keys())
        rows.append(
            pack_row(
                symbol="SPY",
                regime=br,
                strategy_used=strat,
                entry_conf=cf,
                outcome="win" if avg5 > 0 else "loss",
                pnl_pct=avg5,
                hold_h=120.0,
                lesson=les,
                conf_score=cf,
                wins=wc,
                losses=lc,
                regimes_tested=rt or [br],
                seed_tier=tier_m,
            )
        )
    else:
        rejected.append(f"mean_reversion_rsi_extremes: n={n} wr={wr:.2f} conf={cf:.2f} (no tier)")

    # vix_spike
    vs = pats.get("vix_spike_reversion") or {}
    n = int(vs.get("total_occurrences") or 0)
    wr = float(vs.get("win_rate") or 0)
    cf = float(vs.get("confidence_score") or 0)
    tier_v = resolve_seed_tier(n=n, win_rate=wr, laplace_conf=cf)
    if tier_v:
        avg5 = float(vs.get("avg_return_5d") or 0)
        br = str(vs.get("best_regime") or "UNKNOWN")
        strat = "vix_spike_reversion"
        les = _lesson_llm(
            strategy=strat, total_occurrences=n, win_rate=wr, avg_return_5d=avg5, best_regime=br
        ) or _fallback_lesson(strat, n, wr, avg5, br)
        rows.append(
            pack_row(
                symbol="SPY",
                regime=br,
                strategy_used=strat,
                entry_conf=cf,
                outcome="win" if avg5 > 0 else "loss",
                pnl_pct=avg5,
                hold_h=120.0,
                lesson=les,
                conf_score=cf,
                wins=int(vs.get("win_count") or 0),
                losses=int(vs.get("loss_count") or 0),
                regimes_tested=[br],
                seed_tier=tier_v,
            )
        )
    else:
        rejected.append(f"vix_spike_reversion: n={n} wr={wr:.2f} conf={cf:.2f} (no tier)")

    # ma cross 60d
    mc = pats.get("ma_cross_60d") or {}
    n = int(mc.get("total_occurrences") or 0)
    wr = float(mc.get("win_rate") or 0)
    cf = float(mc.get("confidence_score") or 0)
    tier_c = resolve_seed_tier(n=n, win_rate=wr, laplace_conf=cf)
    if tier_c:
        avg60 = float(mc.get("avg_return_60d") or 0)
        br = str(mc.get("best_regime") or "UNKNOWN")
        strat = "golden_death_cross_60d"
        les = _lesson_llm(
            strategy=strat,
            total_occurrences=n,
            win_rate=wr,
            avg_return_5d=avg60 / 12.0,
            best_regime=br,
        ) or _fallback_lesson(strat, n, wr, avg60 / 12.0, br)
        rows.append(
            pack_row(
                symbol="SPY",
                regime=br,
                strategy_used=strat,
                entry_conf=cf,
                outcome="win" if avg60 > 0 else "loss",
                pnl_pct=avg60,
                hold_h=390.0,
                lesson=les,
                conf_score=cf,
                wins=int(mc.get("win_count") or 0),
                losses=int(mc.get("loss_count") or 0),
                regimes_tested=[br],
                seed_tier=tier_c,
            )
        )
    else:
        rejected.append(f"ma_cross_60d: n={n} wr={wr:.2f} conf={cf:.2f} (no tier)")

    # sector rotation — per sector
    sr = pats.get("sector_rotation_rsi_divergence") or {}
    for sec in sr.get("by_sector") or []:
        n = int(sec.get("total_occurrences") or 0)
        wr = float(sec.get("win_rate") or 0)
        cf = float(sec.get("confidence_score") or 0)
        tier_s = resolve_seed_tier(n=n, win_rate=wr, laplace_conf=cf)
        if not tier_s:
            rejected.append(f"sector {sec.get('sector')}: n={n} wr={wr:.2f} (no tier)")
            continue
        sym = str(sec.get("sector") or "XLK")
        px = float(sec.get("avg_excess_return_20d") or 0)
        br = str(sec.get("best_regime") or "UNKNOWN")
        strat = "sector_relative_weakness_excess"
        les = _lesson_llm(
            strategy=strat,
            total_occurrences=n,
            win_rate=wr,
            avg_return_5d=px / 4.0,
            best_regime=br,
        ) or _fallback_lesson(strat, n, wr, px / 4.0, br)
        rows.append(
            pack_row(
                symbol=sym,
                regime=br,
                strategy_used=strat,
                entry_conf=cf,
                outcome="win" if px > 0 else "loss",
                pnl_pct=px,
                hold_h=480.0,
                lesson=les,
                conf_score=cf,
                wins=int(sec.get("win_count") or 0),
                losses=int(sec.get("loss_count") or 0),
                regimes_tested=[br],
                seed_tier=tier_s,
            )
        )

    # post drawdown
    pd_ = pats.get("post_drawdown_recovery") or {}
    n = int(pd_.get("total_occurrences") or 0)
    wr = float(pd_.get("win_rate") or 0)
    cf = float(pd_.get("confidence_score") or 0)
    tier_p = resolve_seed_tier(n=n, win_rate=wr, laplace_conf=cf)
    if tier_p:
        avg60 = float(pd_.get("avg_return_60d") or 0)
        br = str(pd_.get("best_regime") or "UNKNOWN")
        strat = "post_drawdown_recovery_60d"
        les = _lesson_llm(
            strategy=strat,
            total_occurrences=n,
            win_rate=wr,
            avg_return_5d=avg60 / 12.0,
            best_regime=br,
        ) or _fallback_lesson(strat, n, wr, avg60 / 12.0, br)
        rows.append(
            pack_row(
                symbol="SPY",
                regime=br,
                strategy_used=strat,
                entry_conf=cf,
                outcome="win" if avg60 > 0 else "loss",
                pnl_pct=avg60,
                hold_h=390.0,
                lesson=les,
                conf_score=cf,
                wins=int(pd_.get("win_count") or 0),
                losses=int(pd_.get("loss_count") or 0),
                regimes_tested=[br],
                seed_tier=tier_p,
            )
        )
    else:
        rejected.append(f"post_drawdown_recovery: n={n} wr={wr:.2f} conf={cf:.2f} (no tier)")

    em = pats.get("earnings_month_effect") or {}
    delta = float(em.get("delta_pct") or 0)
    rejected.append(
        f"earnings_month_effect: not auto-seeded (needs occurrence-level stats; delta={delta:.6f})"
    )

    return rows, rejected


def playbook_belief_rows(playbooks: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for key, text in playbooks.items():
        cc = _cap_legacy_meta_conf(0.62)
        out.append(
            {
                "belief_id": str(uuid.uuid4()),
                "created_at": _now(),
                "symbol": "META",
                "regime_at_entry": "META",
                "strategy_used": key,
                "entry_signal_confidence": cc,
                "outcome": "win",
                "pnl_pct": 0.0,
                "hold_duration_hours": 0.0,
                "pattern_description": (text[:760] + " [seed tier 2: hypothesis playbook]").strip()[:800],
                "confidence_score": cc,
                "confirmation_count": 1,
                "refutation_count": 0,
                "last_updated_at": _now(),
                "source": "historical_seed",
                "seed_tier": "2",
                "sample_size": 1,
                "date_range": DATE_RANGE,
                "regimes_tested": ["META"],
            }
        )
    return out


def regime_meta_beliefs(transition_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for t in transition_summaries:
        cc = _cap_legacy_meta_conf(0.58)
        rows.append(
            {
                "belief_id": str(uuid.uuid4()),
                "created_at": _now(),
                "symbol": "META",
                "regime_at_entry": "META",
                "strategy_used": f"regime_transition:{t.get('transition', '')}",
                "entry_signal_confidence": cc,
                "outcome": "win",
                "pnl_pct": 0.0,
                "hold_duration_hours": 0.0,
                "pattern_description": str(t.get("summary", ""))[:800],
                "confidence_score": cc,
                "confirmation_count": int(t.get("occurrences") or 0),
                "refutation_count": 0,
                "last_updated_at": _now(),
                "source": "historical_seed",
                "seed_tier": "2",
                "sample_size": int(t.get("occurrences") or 0),
                "date_range": DATE_RANGE,
                "regimes_tested": ["META"],
            }
        )
    return rows
