"""Helpers for deploy patch tests that stub sys.modules."""
from __future__ import annotations

import sys
from types import ModuleType


def stash_sys_modules(*names: str) -> dict[str, ModuleType | None]:
    return {name: sys.modules.get(name) for name in names}


def restore_sys_modules(stash: dict[str, ModuleType | None]) -> None:
    for name, mod in stash.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod
