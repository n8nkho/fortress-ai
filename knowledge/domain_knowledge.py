"""
Structured domain knowledge (strategies, regimes, sectors, risk, behavior).

Persists under ``data/domain_knowledge/*.json`` (repo root). Safe defaults ship in code.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DOMAIN_NAMES = (
    "trading_strategies",
    "market_regimes",
    "sector_dynamics",
    "economic_indicators",
    "risk_frameworks",
    "behavioral_patterns",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


class DomainKnowledge:
    """Load / merge / persist domain JSON blobs."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _repo_root()
        self.knowledge_dir = self.root / "data" / "domain_knowledge"
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._domains: dict[str, dict[str, Any]] = {}
        for name in DOMAIN_NAMES:
            self._domains[name] = self._load_or_initialize(name)

    def _path(self, domain: str) -> Path:
        return self.knowledge_dir / f"{domain}.json"

    def _load_or_initialize(self, domain: str) -> dict[str, Any]:
        file_path = self._path(domain)
        if file_path.exists():
            try:
                raw = json.loads(file_path.read_text(encoding="utf-8"))
                return raw if isinstance(raw, dict) else {}
            except Exception:
                return {}
        base = self.get_base_knowledge(domain)
        file_path.write_text(json.dumps(base, indent=2), encoding="utf-8")
        return dict(base)

    def _persist(self, domain: str) -> None:
        if domain not in DOMAIN_NAMES:
            return
        self._path(domain).write_text(
            json.dumps(self._domains.get(domain, {}), indent=2, default=str),
            encoding="utf-8",
        )

    def get_domain(self, domain: str) -> dict[str, Any]:
        return dict(self._domains.get(domain, {}))

    def get_base_knowledge(self, domain: str) -> dict[str, Any]:
        if domain == "trading_strategies":
            return {
                "mean_reversion": {
                    "principle": "Prices tend to revert toward a local mean after sharp dislocations.",
                    "best_conditions": ["ranging_markets", "low_to_moderate_volatility", "neutral_regime"],
                    "indicators": ["RSI", "Bollinger_Bands", "price_vs_moving_average"],
                    "entry_signals": ["oversold_with_volume_confirmation", "support_bounce"],
                    "exit_signals": ["return_to_mean", "resistance_hit", "time_stop"],
                    "failure_modes": ["strong_trend", "structural_break", "unexpected_catalyst"],
                    "notes": "Favor confirmation over raw RSI alone.",
                },
                "momentum": {
                    "principle": "Trend persistence while liquidity and breadth support continuation.",
                    "best_conditions": ["trending_markets", "high_volume", "sector_rotation"],
                    "indicators": ["rate_of_change", "MACD", "relative_strength"],
                    "failure_modes": ["exhaustion", "distribution", "regime_change"],
                },
            }
        if domain == "market_regimes":
            return {
                "BULL_TREND": {
                    "characteristics": ["higher_highs", "higher_lows", "support_on_pullbacks"],
                    "best_strategies": ["momentum", "breakout", "growth_tilts"],
                    "avoid_strategies": ["aggressive_shorting", "fade_every_rally"],
                    "early_warning_signs": ["breadth_divergence", "credit_stress"],
                },
                "NEUTRAL_RANGING": {
                    "characteristics": ["choppy_price_action", "mean_reversion_more_reliable"],
                    "best_strategies": ["mean_reversion", "pairs_trading", "defined_risk_options"],
                    "typical_vix_range": [12, 22],
                },
                "BEAR_TREND": {
                    "characteristics": ["lower_lows", "lower_highs", "risk_off_rotation"],
                    "best_strategies": ["trend_following_shorts", "hedging", "raise_cash"],
                    "avoid_strategies": ["catch_falling_knife_longs"],
                },
            }
        if domain == "sector_dynamics":
            return {
                "technology": {
                    "characteristics": ["high_beta", "momentum_sensitive", "liquidity_deep"],
                    "interest_rate_sensitivity": "typically_negative_for_long_duration_growth",
                    "key_metrics": ["revenue_growth", "margins", "capex_cycle"],
                },
                "utilities": {
                    "characteristics": ["defensive", "yield_focused", "lower_beta"],
                    "mean_reversion_friendly": True,
                },
                "financials": {
                    "characteristics": ["rate_sensitive", "credit_cycle_linked"],
                },
            }
        if domain == "economic_indicators":
            return {
                "fed_policy": {
                    "states": ["easing", "neutral", "tightening"],
                    "market_impact": {
                        "easing": "supportive_for_risk_assets_trend",
                        "tightening": "headwind_for_high_duration_assets",
                    },
                    "lag_effect_months": 6,
                },
                "employment": {
                    "key_reports": ["NFP", "unemployment_rate", "jobless_claims"],
                    "interpretation": "Strong labor can be mixed: supports consumption but can tighten financial conditions.",
                },
            }
        if domain == "risk_frameworks":
            return {
                "position_sizing": {
                    "kelly_criterion": "fractional_kelly_common_in_practice",
                    "volatility_adjusted": "size_inverse_to_realized_vol",
                    "typical_range_pct_equity": [1, 5],
                },
                "correlation_risk": {
                    "principle": "Correlations spike_in_stress",
                    "guidance": "Cap_correlated_exposure_across_book",
                },
            }
        if domain == "behavioral_patterns":
            return {
                "panic_selling": {
                    "indicators": ["volume_spike", "rapid_price_drop", "vix_spike"],
                    "opportunity": "mean_reversion_after_capitulation_requires_confirmation",
                },
                "euphoria": {
                    "indicators": ["complacency", "compressed_volatility", "crowded_long"],
                    "risk": "mean_reversion_risk_rises",
                },
            }
        return {}

    def query_knowledge(self, domain: str, concept: str) -> dict[str, Any]:
        base = self.get_domain(domain)
        v = base.get(concept)
        return v if isinstance(v, dict) else {}

    def get_relevant_knowledge(self, context: dict[str, Any]) -> dict[str, Any]:
        regime = str(context.get("regime") or "NEUTRAL_RANGING").strip().upper()
        if regime not in self.get_domain("market_regimes"):
            regime = "NEUTRAL_RANGING"
        strategy = str(context.get("strategy") or "mean_reversion").strip().lower()
        sector_raw = str(context.get("sector") or "").strip().lower()
        sectors = self.get_domain("sector_dynamics")
        sector_key = sector_raw if sector_raw in sectors else ""

        return {
            "regime_knowledge": self.get_domain("market_regimes").get(regime, {}),
            "strategy_knowledge": self.get_domain("trading_strategies").get(strategy, {}),
            "sector_knowledge": sectors.get(sector_key, {}),
            "risk_knowledge": self.get_domain("risk_frameworks"),
            "behavior_knowledge": self.get_domain("behavioral_patterns"),
        }

    def add_learned_knowledge(self, domain: str, concept: str, knowledge: dict[str, Any]) -> None:
        if domain not in DOMAIN_NAMES:
            domain = "behavioral_patterns"
        base = self.get_domain(domain)
        if concept in base and isinstance(base[concept], dict) and isinstance(knowledge, dict):
            merged = {**base[concept], **knowledge}
            base[concept] = merged
        else:
            base[concept] = knowledge
        self._domains[domain] = base
        self._persist(domain)

    def concept_counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._domains.items() if isinstance(v, dict)}
