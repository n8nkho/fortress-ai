"""Base types for portfolio session entry guards."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GuardResult:
    blocked: bool = False
    reason: str = ""
    detail: str = ""
    guard: str = ""


class BaseGuard:
    name: str = "base"

    def evaluate(self, session_state: dict) -> GuardResult:
        raise NotImplementedError

    def evaluate_session(self, session_state: dict) -> GuardResult:
        """Alias used by session_manager entry pipeline."""
        return self.evaluate(session_state)
