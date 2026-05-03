"""
Domain-aware reasoning facade (prompt injection + UI snapshot helpers).

Uses DeepSeek via ``unified_ai_agent.call_deepseek`` only when explicitly wired;
default path is structured knowledge in the prompt (see ``knowledge.intel``).
"""
from __future__ import annotations

from typing import Any

from knowledge.intel import (
    build_domain_prompt_appendix,
    domain_intel_snapshot,
    infer_regime,
    infer_sector,
    infer_strategy,
)


class DomainReasoningEngine:
    """Thin wrapper so callers can depend on a single import surface."""

    def prompt_appendix(self, observation: dict[str, Any], state: dict[str, Any]) -> str:
        return build_domain_prompt_appendix(observation, state)

    def snapshot(self, macro: dict[str, Any] | None, beliefs: dict[str, Any] | None = None) -> dict[str, Any]:
        return domain_intel_snapshot(macro or {}, beliefs=beliefs or {})

    def regime_label(self, observation: dict[str, Any]) -> str:
        return infer_regime(observation)

    def sector_for_ticker(self, ticker: str | None) -> str:
        return infer_sector(ticker)

    def strategy_label(self, observation: dict[str, Any], state: dict[str, Any]) -> str:
        return infer_strategy(observation, state)
