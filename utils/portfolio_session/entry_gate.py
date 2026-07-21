"""Portfolio session entry gate — evaluates macro guards before swarm entries."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from utils.portfolio_session.entry_guard_manager import get_entry_guards
from utils.portfolio_session.entry_guards import (
    evaluate_entry_blocks as _evaluate_entry_guard_blocks,
    market_relative_underperformance_gate,
)
from utils.portfolio_session.guards import BaseGuard, GuardResult
from utils.portfolio_session.metrics.session_alpha import enrich_session_context_with_alpha
from utils.portfolio_session.risk_manager import build_session_state as _build_session_state
from utils.portfolio_session.risk_manager import load_market_relative_gate_config
from utils.portfolio_session.session_state import SessionState

log = logging.getLogger(__name__)


def load_guard_config() -> dict[str, Any]:
    """Alias for market-relative guard config (portfolio session guards.yaml)."""
    return load_market_relative_gate_config()


def _build_guards() -> list[BaseGuard]:
    """Market-relative underperformance runs first among portfolio session macro guards."""
    return get_entry_guards()


class EntryGate:
    def __init__(self, guards: list[BaseGuard] | None = None) -> None:
        self.market_relative_blocked = False
        self.guards = guards if guards is not None else _build_guards()

    def check_market_relative_underperformance(
        self,
        session_state: dict[str, Any] | None = None,
    ) -> bool:
        """Block when session alpha vs SPY is below the immutable threshold."""
        from utils.portfolio_session.config import get_market_relative_underperformance_threshold

        state = enrich_session_context_with_alpha(_build_session_state(session_state=session_state))
        snapshot = SessionState(state).update(state)
        alpha = snapshot.get("session_alpha_vs_spy")
        threshold = get_market_relative_underperformance_threshold()

        if alpha is None or not snapshot.get("benchmark_ok", True):
            self.market_relative_blocked = False
            return False

        if market_relative_underperformance_gate(alpha, threshold):
            self.market_relative_blocked = True
            bps = int(round(abs(threshold) * 100))
            log.warning(
                "entry_blocked_by_market_relative market_relative_underperformance "
                "MarketRelativeGate session_underperforming "
                "alpha_vs_spy_pct=%.4f market_relative_underperformance_threshold=%.4f "
                "market_relative_underperformance_threshold_bps=%s swarm_gate_order_specific_before_macro",
                float(alpha),
                threshold,
                bps,
            )
            return True

        self.market_relative_blocked = False
        return False

    def _run_guard(self, guard: BaseGuard, state: dict[str, Any]) -> GuardResult:
        if hasattr(guard, "evaluate_session"):
            return guard.evaluate_session(state)
        return guard.evaluate(state)

    def check_entry_blocks(self, session_state: dict[str, Any] | None = None) -> list[str]:
        """Run configured entry guards; return block reason codes when any guard triggers."""
        reasons: list[str] = []
        guard_blocks = _evaluate_entry_guard_blocks(session_state)
        if guard_blocks.get("blocked"):
            reasons.append(str(guard_blocks.get("reason") or "market_relative_underperformance"))
            return reasons

        state = enrich_session_context_with_alpha(_build_session_state(session_state=session_state))
        for guard in self.guards:
            result = self._run_guard(guard, state)
            if result.blocked:
                log.info("entry_blocked_by_market_relative %s", result.detail or result.reason)
                reasons.append(
                    result.reason or getattr(guard, "name", "market_relative_underperformance")
                )
        return reasons

    def evaluate_entry_blocks(self, session_state: dict[str, Any] | None = None) -> dict[str, Any]:
        """Evaluate macro entry guards; market-relative check runs first."""
        block_reasons = self.check_entry_blocks(session_state)
        if block_reasons:
            self.market_relative_blocked = True
            guard_blocks = _evaluate_entry_guard_blocks(session_state)
            result: dict[str, Any] = {
                "blocked": True,
                "reason": block_reasons[0],
                "market_relative_underperformance": True,
                "block_reasons": block_reasons,
            }
            if guard_blocks.get("entry_block_breakdown"):
                result["entry_block_breakdown"] = guard_blocks["entry_block_breakdown"]
            if guard_blocks.get("detail"):
                result["detail"] = guard_blocks["detail"]
            return result

        self.market_relative_blocked = False
        return {
            "blocked": False,
            "reason": "",
            "market_relative_underperformance": False,
        }

    def evaluate(self, session_state: dict[str, Any] | None = None) -> GuardResult:
        blocks = self.evaluate_entry_blocks(session_state=session_state)
        if blocks.get("blocked"):
            return GuardResult(
                blocked=True,
                reason=str(blocks.get("reason") or "market_relative_underperformance"),
                guard="market_relative_underperformance",
            )
        return GuardResult(blocked=False)


@lru_cache(maxsize=1)
def get_entry_gate() -> EntryGate:
    return EntryGate()


def evaluate_entry_gate(session_state: dict[str, Any] | None = None) -> GuardResult:
    return get_entry_gate().evaluate(session_state=session_state)


def evaluate_entry_blocks(session_state: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_entry_gate().evaluate_entry_blocks(session_state=session_state)
