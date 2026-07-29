"""Base types for fortress risk entry guards."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GuardResult:
    blocked: bool = False
    reason: str = ""
    detail: str = ""
    guard: str = ""


class BaseGuard:
    name: str = "base"

    def evaluate(self, *args, **kwargs) -> GuardResult:
        raise NotImplementedError
