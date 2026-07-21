"""Portfolio session pre-trade gate registry."""
from __future__ import annotations

from utils.portfolio_session.gates.base import BaseGate, GateResult
from utils.portfolio_session.gates.market_relative_gate import MarketRelativeGate
from utils.portfolio_session.gates.market_relative_underperformance_gate import (
    MarketRelativeUnderperformanceGate,
)

GATE_REGISTRY: dict[str, type[BaseGate]] = {
    "market_relative": MarketRelativeGate,
    "market_relative_underperformance": MarketRelativeUnderperformanceGate,
}

PRE_TRADE_GATES: list[type[BaseGate]] = [
    MarketRelativeUnderperformanceGate,
]

__all__ = [
    "BaseGate",
    "GATE_REGISTRY",
    "GateResult",
    "MarketRelativeGate",
    "MarketRelativeUnderperformanceGate",
    "PRE_TRADE_GATES",
]
