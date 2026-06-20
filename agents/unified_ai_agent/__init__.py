"""Unified AI agent package — core implementation + exit ledger helpers."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_EXPORTS = (
    "act",
    "observe",
    "reason",
    "main",
    "run_loop",
    "call_deepseek",
    "_parse_llm_json",
    "_alpaca_client",
    "_maybe_flatten_legacy_positions",
    "_dry_run",
    "load_state",
    "save_state",
    "append_decision",
    "append_metric",
    "build_prompt",
)


def _load_core() -> ModuleType:
    core_path = Path(__file__).resolve().parent.parent / "unified_ai_agent.py"
    name = "agents.unified_ai_agent._core"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, core_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load unified agent core from {core_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_core = _load_core()
for _name in _EXPORTS:
    globals()[_name] = getattr(_core, _name)

__all__ = list(_EXPORTS)
