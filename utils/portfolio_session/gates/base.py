"""Base types for portfolio session pre-trade gates."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateResult:
    blocked: bool = False
    reason: str = ""
    detail: str = ""
    gate: str = ""


class BaseGate:
    name: str = "base"

    def evaluate(self, session_state: dict) -> GateResult:
        raise NotImplementedError
