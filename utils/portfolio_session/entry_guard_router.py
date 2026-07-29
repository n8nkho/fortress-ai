"""Route portfolio session entry requests through the configured guard chain."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from utils.portfolio_session.guards import (
    GUARD_REGISTRY,
    BaseGuard,
    GuardResult,
    MarketRelativeUnderperformanceGuard,
)
from utils.portfolio_session.metrics.session_alpha import enrich_session_context_with_alpha
from utils.portfolio_session.risk_manager import load_market_relative_guard_config

log = logging.getLogger(__name__)

_DEFAULT_GUARDS_YAML = Path(__file__).resolve().parent / "config" / "default_guards.yaml"

# market_relative slot first, then market_relative_underperformance (SI plan order).
GUARD_CHAIN: tuple[str, ...] = (
    "market_relative",
    "market_relative_underperformance",
)


def _load_yaml_section(path: Path, *keys: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml

        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(doc, dict):
        return {}
    node: Any = doc
    for key in keys:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return dict(node) if isinstance(node, dict) else {}


def load_guard_chain_config(config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Merge default_guards.yaml sections with runtime overrides."""
    merged: dict[str, dict[str, Any]] = {}
    runtime = dict(config or load_market_relative_guard_config())
    for name in GUARD_CHAIN:
        section = _load_yaml_section(_DEFAULT_GUARDS_YAML, name)
        if name == "market_relative_underperformance" and runtime:
            section = {**section, **runtime}
        merged[name] = section
    return merged


def _resolve_threshold(cfg: dict[str, Any]) -> float | None:
    for key in (
        "underperformance_threshold",
        "underperformance_threshold_pct",
        "market_relative_underperformance_threshold_pct",
        "threshold_alpha_pp",
        "threshold_pct",
        "threshold_alpha_vs_spy_pct",
    ):
        if key in cfg:
            return cfg[key]
    return None


def build_entry_guards(config: dict[str, Any] | None = None) -> list[BaseGuard]:
    """Build guard instances in chain order (market_relative then underperformance)."""
    chain_cfg = load_guard_chain_config(config)
    guards: list[BaseGuard] = []
    for name in GUARD_CHAIN:
        guard_cls = GUARD_REGISTRY.get(name)
        if guard_cls is None:
            continue
        section = dict(chain_cfg.get(name) or {})
        if not bool(section.get("enabled", True)):
            continue
        threshold = _resolve_threshold(section)
        kwargs: dict[str, Any] = {"config": section, "enabled": True}
        if threshold is not None:
            kwargs["underperformance_threshold"] = threshold
            kwargs["threshold_pct"] = threshold
        guards.append(guard_cls(**kwargs))
    if not guards:
        guards.append(MarketRelativeUnderperformanceGuard(config=dict(config or {})))
    return guards


def evaluate_guard_chain(
    session_state: dict[str, Any] | None = None,
    *,
    guards: list[BaseGuard] | None = None,
    config: dict[str, Any] | None = None,
) -> GuardResult:
    """Run guards in order; return the first block or pass."""
    state = enrich_session_context_with_alpha(dict(session_state or {}))
    state.setdefault("component", "portfolio_session")
    active = guards if guards is not None else build_entry_guards(config)
    for guard in active:
        check_fn = getattr(guard, "check_session_context", None)
        if callable(check_fn):
            result = check_fn(state)
        else:
            result = guard.evaluate(state)
        if result.blocked:
            log.warning(
                "entry_blocked_by_market_relative market_relative_underperformance "
                "MarketRelativeGate swarm_gate_order_specific_before_macro %s",
                result.detail or result.reason,
            )
            return result
    return GuardResult(blocked=False, guard="entry_guard_router")


def get_entry_guards(config: dict[str, Any] | None = None) -> list[BaseGuard]:
    """Return active entry guards from the router."""
    return build_entry_guards(config)


__all__ = [
    "GUARD_CHAIN",
    "build_entry_guards",
    "evaluate_guard_chain",
    "get_entry_guards",
    "load_guard_chain_config",
]
