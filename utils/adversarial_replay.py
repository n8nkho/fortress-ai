"""Adversarial perturbations on replayed trades — stress policies, not synthetic prices."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from agents.skim_swarm.scenario_stress import (
    OverlayResult,
    TradeRecord,
    adjust_exit_pnl,
    entry_would_fire,
    score_overlay,
)


@dataclass
class AdversarialSpec:
    slippage_usd: float = 0.02
    stop_worsen_mult: float = 1.08
    target_shrink_mult: float = 0.92


def _default_spec() -> AdversarialSpec:
    return AdversarialSpec()


def adversarial_adjust_exit_pnl(
    trade: TradeRecord,
    overlay: dict[str, Any],
    *,
    spec: AdversarialSpec | None = None,
) -> float:
    """Apply overlay exit scaling then adversarial slippage / stop worsening."""
    spec = spec or _default_spec()
    pnl = adjust_exit_pnl(trade, overlay)
    if trade.exit_reason == "stop_loss" and pnl < 0:
        pnl = round(pnl * spec.stop_worsen_mult, 4)
    elif trade.exit_reason == "target_hit" and pnl > 0:
        pnl = round(pnl * spec.target_shrink_mult, 4)
    return round(pnl - spec.slippage_usd, 4)


def score_overlay_adversarial(
    trades: Iterable[TradeRecord],
    overlay: dict[str, Any],
    *,
    spec: AdversarialSpec | None = None,
) -> OverlayResult:
    spec = spec or _default_spec()
    blocked = 0
    pnls: list[float] = []
    for t in trades:
        if not entry_would_fire(t, overlay):
            blocked += 1
            continue
        pnls.append(adversarial_adjust_exit_pnl(t, overlay, spec=spec))
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


def adversarial_improves_vs_baseline(
    trades: list[TradeRecord],
    overlay: dict[str, Any],
    *,
    min_improvement_usd: float = 0.05,
    spec: AdversarialSpec | None = None,
) -> dict[str, Any]:
    baseline = score_overlay_adversarial(trades, {"disable_patterns": []}, spec=spec)
    stressed = score_overlay_adversarial(trades, overlay, spec=spec)
    delta = stressed.sum_pnl_usd - baseline.sum_pnl_usd
    return {
        "baseline_pnl": baseline.sum_pnl_usd,
        "overlay_pnl": stressed.sum_pnl_usd,
        "delta_usd": round(delta, 4),
        "passes": delta >= min_improvement_usd,
        "baseline_exits": baseline.exits,
        "overlay_exits": stressed.exits,
    }
