"""Track deployed code-guard fixes so integrity scans skip historical false positives."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.system_time import ensure_system_tz, now, now_iso, parse_iso, system_tz_name

ensure_system_tz()

_ROOT = Path(__file__).resolve().parent.parent

# Source files checked for mitigation markers per fix code
_GUARD_SOURCES: dict[str, list[Path]] = {
    "duplicate_entry_accumulation": [
        _ROOT / "agents" / "unified_ai_agent.py",
        _ROOT / "agents" / "unified_agent.py",
        _ROOT / "agents" / "unified_ai.py",
        _ROOT / "agents" / "unified_ai" / "entry_guard.py",
        _ROOT / "agents" / "unified_ai" / "exit_planner.py",
        _ROOT / "agents" / "unified_ai" / "position_manager.py",
        _ROOT / "config" / "unified_ai_defaults.py",
        _ROOT / "utils" / "unified_enter_guard.py",
        _ROOT / "utils" / "order_chunking.py",
        _ROOT / "risk" / "order_sizing.py",
        _ROOT / "risk" / "order_chunker.py",
        _ROOT / "risk" / "legacy_flattener.py",
        _ROOT / "risk" / "position_manager.py",
        _ROOT / "risk" / "pre_trade_gate.py",
        _ROOT / "config" / "defaults.py",
        _ROOT / "config" / "risk_params.py",
        _ROOT / "config" / "risk_config.py",
        _ROOT / "config" / "constants.py",
        _ROOT / "unified_ai" / "config.py",
        _ROOT / "unified_ai" / "entry_gate.py",
        _ROOT / "unified_ai" / "exit_manager.py",
        _ROOT / "unified_ai" / "position_manager.py",
        _ROOT / "unified_ai" / "order_executor.py",
        _ROOT / "unified_ai" / "order_utils.py",
        _ROOT / "unified_ai" / "order_chunker.py",
        _ROOT / "unified_ai" / "legacy_flattener.py",
        _ROOT / "unified_ai" / "agent.py",
        _ROOT / "unified_ai" / "order_router.py",
        _ROOT / "unified_ai" / "startup.py",
        _ROOT / "unified_ai" / "risk_controller.py",
    ],
    "exit_notional_blocked": [
        _ROOT / "agents" / "unified_ai_agent.py",
        _ROOT / "agents" / "skim_swarm" / "act.py",
    ],
    "skim_qty_invalid_exits": [
        _ROOT / "agents" / "skim_swarm" / "act.py",
    ],
    "halt_blocked_exit": [
        _ROOT / "agents" / "skim_swarm" / "signal.py",
        _ROOT / "agents" / "infra_swarm" / "signal.py",
    ],
    "swarm_universe_drift": [
        _ROOT / "agents" / "skim_swarm_agent.py",
        _ROOT / "agents" / "infra_swarm_agent.py",
        _ROOT / "utils" / "swarm_runtime.py",
    ],
    "swarm_orphan_symbol_entry": [
        _ROOT / "utils" / "swarm_universe_guard.py",
        _ROOT / "agents" / "skim_swarm" / "signal.py",
    ],
    "alpaca_bracket_tick_violation": [
        _ROOT / "utils" / "edge_quality.py",
        _ROOT / "utils" / "alpaca_execution.py",
    ],
    "swarm_critical_pause_entries": [
        _ROOT / "utils" / "swarm_session_si.py",
        _ROOT / "agents" / "skim_swarm" / "signal.py",
        _ROOT / "agents" / "infra_swarm" / "signal.py",
    ],
    "unified_off_denylist_watchlist": [
        _ROOT / "utils" / "unified_symbol_pool.py",
        _ROOT / "agents" / "unified_ai_agent.py",
    ],
    "edge_rr_cost_gates": [
        _ROOT / "utils" / "edge_quality.py",
        _ROOT / "agents" / "skim_swarm" / "signal.py",
    ],
    "swarm_pnl_decisions_sync": [
        _ROOT / "utils" / "swarm_decisions_pnl.py",
        _ROOT / "utils" / "swarm_pnl_ledger.py",
        _ROOT / "agents" / "skim_swarm" / "symbol_learning.py",
    ],
    "market_relative_si_benchmark": [
        _ROOT / "utils" / "market_benchmark.py",
        _ROOT / "utils" / "portfolio_session" / "entry_manager.py",
        _ROOT / "utils" / "portfolio_session" / "session_summary.py",
        _ROOT / "utils" / "portfolio_session" / "reporting.py",
    ],
    "market_relative_underperformance": [
        _ROOT / "utils" / "market_benchmark.py",
        _ROOT / "config" / "defaults.yaml",
        _ROOT / "config" / "portfolio_session.yaml",
        _ROOT / "config" / "session_guards.yaml",
        _ROOT / "config" / "risk_guards.yaml",
        _ROOT / "risk" / "guards" / "market_relative_guard.py",
        _ROOT / "docs" / "risk_guards.md",
        _ROOT / "risk" / "guards" / "market_relative_underperformance.py",
        _ROOT / "risk" / "guard_engine.py",
        _ROOT / "config" / "guards.yaml",
        _ROOT / "config" / "trading_bot_defaults.yaml",
        _ROOT / "utils" / "portfolio_session" / "config.py",
        _ROOT / "utils" / "portfolio_session" / "entry_blocks.py",
        _ROOT / "utils" / "portfolio_session" / "si_finding.py",
        _ROOT / "utils" / "portfolio_session" / "guards" / "market_relative.py",
        _ROOT / "utils" / "portfolio_session" / "entry_decision.py",
        _ROOT / "utils" / "portfolio_session" / "session_metrics.py",
        _ROOT / "utils" / "portfolio_session" / "guards" / "market_relative_guard.py",
        _ROOT / "utils" / "portfolio_session" / "guards" / "base.py",
        _ROOT / "utils" / "portfolio_session" / "entry_gate.py",
        _ROOT / "utils" / "portfolio_session" / "session_state.py",
        _ROOT / "utils" / "portfolio_session" / "entry_guard_manager.py",
        _ROOT / "utils" / "portfolio_session" / "entry_block_manager.py",
        _ROOT / "utils" / "portfolio_session" / "metrics" / "session_alpha.py",
        _ROOT / "utils" / "portfolio_session" / "config" / "default_guards.yaml",
        _ROOT / "utils" / "portfolio_session" / "config" / "default.yaml",
        _ROOT / "utils" / "portfolio_session" / "config" / "guards.yaml",
        _ROOT / "utils" / "portfolio_session" / "gates" / "market_relative_gate.py",
        _ROOT / "utils" / "portfolio_session" / "gates" / "__init__.py",
        _ROOT / "utils" / "portfolio_session" / "config" / "guard_config.yaml",
        _ROOT / "utils" / "portfolio_session" / "risk_manager.py",
        _ROOT / "utils" / "portfolio_session" / "session_manager.py",
        _ROOT / "utils" / "portfolio_session" / "entry_manager.py",
        _ROOT / "monitoring" / "alerts.py",
        _ROOT / "monitoring" / "__init__.py",
        _ROOT / "utils" / "portfolio_session" / "entry_guards.py",
        _ROOT / "utils" / "portfolio_session" / "session_state.py",
        _ROOT / "tests" / "test_entry_guards.py",
        _ROOT / "tests" / "portfolio_session" / "test_entry_blocks.py",
        _ROOT / "tests" / "portfolio_session" / "guards" / "test_market_relative_underperformance.py",
        _ROOT / "tests" / "portfolio_session" / "test_market_relative_guard.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "entry_blocks.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "config.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "entry_guards.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "entry_guards" / "__init__.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "entry_guards" / "base.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "entry_guards" / "market_relative_underperformance.py",
        _ROOT / "deploy" / "trading-bot-patches" / "config" / "guards.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "entry_gate.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "session_state.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "session_manager.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "__init__.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_entry_gate.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_entry_guards.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_guards.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "unit" / "portfolio" / "session" / "entry_guards" / "test_market_relative_underperformance.py",
        _ROOT / "deploy" / "trading-bot-patches" / "risk" / "entry_blocker.py",
        _ROOT / "deploy" / "trading-bot-patches" / "risk" / "entry_block_aggregator.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_entry_blocker.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_entry_block_aggregator.py",
        _ROOT / "tests" / "portfolio_session" / "test_market_relative_underperformance.py",
        _ROOT / "agents" / "skim_swarm" / "signal.py",
        _ROOT / "agents" / "infra_swarm" / "signal.py",
        _ROOT / "tests" / "test_market_relative_underperformance.py",
        _ROOT / "tests" / "risk" / "gates" / "test_market_relative_underperformance.py",
        _ROOT / "deploy" / "trading-bot-patches" / "core" / "session_tracker.py",
        _ROOT / "deploy" / "trading-bot-patches" / "core" / "entry_gate.py",
        _ROOT / "deploy" / "trading-bot-patches" / "core" / "session_metrics.py",
        _ROOT / "deploy" / "trading-bot-patches" / "config" / "session_guards.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "guards.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "guards" / "market_relative.py",
        _ROOT / "utils" / "portfolio_session" / "guards" / "market_relative_underperformance.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "entry_decision.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "session_metrics.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "guards" / "market_relative_guard.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "guards" / "base.py",
        _ROOT / "deploy" / "trading-bot-patches" / "config" / "portfolio_session.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "guards" / "__init__.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "config" / "default_guards.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "config" / "default.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "entry_guard_manager.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "entry_block_manager.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "metrics" / "session_alpha.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_market_relative_guard.py",
        _ROOT / "tests" / "test_market_relative_guard.py",
        _ROOT / "tests" / "test_src_market_relative_guard.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "guards" / "market_relative_underperformance.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "guards" / "market_relative_guard.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "config" / "guards.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "guards" / "base.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "guards" / "__init__.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "guards" / "registry.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "entry_manager.py",
        _ROOT / "deploy" / "trading-bot-patches" / "config" / "guard_config.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_market_relative_underperformance_guard.py",
        _ROOT / "tests" / "test_market_relative_underperformance_guard.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "config" / "guard_defaults.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_market_relative_underperformance.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "portfolio_session" / "test_market_relative_underperformance.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "config" / "guard_config.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "config" / "__init__.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "entry" / "entry_gate.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "portfolio" / "session.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "portfolio_session" / "__init__.py",
        _ROOT / "deploy" / "trading-bot-patches" / "src" / "portfolio_session" / "session_manager.py",
        _ROOT / "deploy" / "trading-bot-patches" / "config" / "default.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_src_market_relative_guard.py",
        _ROOT / "deploy" / "trading-bot-patches" / "config" / "guard_defaults.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_entry_blocks.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "session_entry_blocks.py",
        _ROOT / "scripts" / "apply_trading_bot_market_relative_config.py",
    ],
    "negative_alpha_active_session": [
        _ROOT / "utils" / "portfolio_session" / "alpha_monitor.py",
        _ROOT / "utils" / "portfolio_session" / "session_manager.py",
        _ROOT / "utils" / "portfolio_session" / "config.py",
        _ROOT / "config" / "session_config.yaml",
        _ROOT / "tests" / "portfolio_session" / "test_alpha_monitor.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "alpha_monitor.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "session_manager.py",
        _ROOT / "deploy" / "trading-bot-patches" / "portfolio_session" / "config.py",
        _ROOT / "deploy" / "trading-bot-patches" / "config" / "session_config.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_alpha_monitor.py",
        _ROOT / "scripts" / "apply_trading_bot_market_relative_config.py",
    ],
    "broker_open_sell_backlog": [
        _ROOT / "deploy" / "trading-bot-patches" / "alpaca_execution" / "order_manager.py",
        _ROOT / "deploy" / "trading-bot-patches" / "alpaca_execution" / "execution_engine.py",
        _ROOT / "deploy" / "trading-bot-patches" / "alpaca_execution" / "cleanup_scheduler.py",
        _ROOT / "deploy" / "trading-bot-patches" / "alpaca_execution" / "exit_gate.py",
        _ROOT / "deploy" / "trading-bot-patches" / "alpaca_execution" / "exit_controller.py",
        _ROOT / "deploy" / "trading-bot-patches" / "alpaca_execution" / "order_lifecycle.py",
        _ROOT / "deploy" / "trading-bot-patches" / "alpaca_execution" / "order_status_handler.py",
        _ROOT / "deploy" / "trading-bot-patches" / "alpaca_execution" / "exit_handler.py",
        _ROOT / "deploy" / "trading-bot-patches" / "alpaca_execution" / "constants.py",
        _ROOT / "deploy" / "trading-bot-patches" / "config" / "alpaca.yaml",
        _ROOT / "deploy" / "trading-bot-patches" / "config" / "alpaca_settings.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_order_lifecycle.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_exit_handler.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_alpaca_order_manager.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_order_manager.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_alpaca_execution.py",
        _ROOT / "deploy" / "trading-bot-patches" / "tests" / "test_execution_engine.py",
        _ROOT / "scripts" / "apply_trading_bot_broker_open_sell_backlog.py",
        _ROOT / "tests" / "test_broker_open_sell_backlog.py",
        _ROOT / "utils" / "alpaca_order_hygiene.py",
        _ROOT / "utils" / "alpaca_execution.py",
        _ROOT / "agents" / "unified_ai_agent.py",
        _ROOT / "agents" / "skim_swarm" / "act.py",
        _ROOT / "unified_ai" / "order_router.py",
        _ROOT / "unified_ai" / "order_executor.py",
        _ROOT / "unified_ai" / "startup.py",
        _ROOT / "unified_ai" / "main_loop.py",
        _ROOT / "utils" / "operator_broker_reconcile.py",
    ],
    "consciousness_kb_stale": [
        _ROOT / "utils" / "market_consciousness_knowledge_base.py",
        _ROOT / "utils" / "market_consciousness" / "knowledge_base.py",
        _ROOT / "utils" / "market_consciousness_scheduler.py",
        _ROOT / "utils" / "si_queue" / "handlers.py",
        _ROOT / "utils" / "si_queue" / "si_processor.py",
        _ROOT / "scripts" / "build_hourly_market_knowledge.py",
        _ROOT / "config" / "constants.py",
        _ROOT / "config" / "market_consciousness.yaml",
    ],
    "premature_exit_ledger": [
        _ROOT / "agents" / "unified_ai_agent.py",
        _ROOT / "agents" / "unified_ai_agent" / "exit_handler.py",
        _ROOT / "agents" / "unified_ai_agent" / "broker_integration.py",
        _ROOT / "agents" / "unified_ai_agent" / "ledger.py",
        _ROOT / "agents" / "unified_ai_agent" / "reconciliation.py",
        _ROOT / "utils" / "broker_reconciliation.py",
    ],
    "operator_broker_open_drift": [
        _ROOT / "utils" / "operator_broker_reconcile.py",
        _ROOT / "utils" / "operator_status_report.py",
        _ROOT / "scripts" / "operator_status_report.py",
        _ROOT / "utils" / "broker_reconciliation.py",
    ],
    "exit_unfilled_pnl_ledger": [
        _ROOT / "agents" / "unified_ai_agent.py",
        _ROOT / "agents" / "unified_ai_agent" / "exit_handler.py",
        _ROOT / "agents" / "unified_ai_agent" / "broker_integration.py",
        _ROOT / "agents" / "unified_ai_agent" / "ledger.py",
        _ROOT / "utils" / "broker_reconciliation.py",
    ],
    "constructive_tape_entry_override": [
        _ROOT / "utils" / "portfolio_session" / "constructive_tape_override.py",
        _ROOT / "utils" / "portfolio_session" / "gates" / "market_relative_underperformance_gate.py",
        _ROOT / "utils" / "portfolio_session" / "gates" / "market_relative_gate.py",
        _ROOT / "utils" / "portfolio_session" / "entry_decision.py",
        _ROOT / "utils" / "portfolio_session" / "entry_guards" / "__init__.py",
        _ROOT / "config" / "portfolio_session.yaml",
        _ROOT / "tests" / "test_constructive_tape_override.py",
        _ROOT / ".env.example",
    ],
    "market_participation_conflict": [
        _ROOT / "utils" / "portfolio_session" / "constructive_tape_override.py",
        _ROOT / "utils" / "portfolio_session" / "gates" / "market_relative_underperformance_gate.py",
        _ROOT / "utils" / "market_benchmark.py",
        _ROOT / "config" / "si_objectives.json",
        _ROOT / "tests" / "test_constructive_tape_override.py",
    ],
    "classic_sleeve_demoted": [
        _ROOT / "utils" / "si_recommendation_queue.py",
        _ROOT / "utils" / "si_capability_review.py",
        _ROOT / "config" / "si_objectives.json",
        _ROOT / "tests" / "test_constructive_tape_override.py",
    ],
    "si_intervention_effectiveness_gap": [
        _ROOT / "utils" / "si_intervention_log.py",
        _ROOT / "utils" / "edge_autofix.py",
        _ROOT / "utils" / "portfolio_session" / "constructive_tape_override.py",
        _ROOT / "utils" / "si_recommendation_queue.py",
        _ROOT / "config" / "si_fix_registry.json",
    ],
    "si_gap_review_only_findings": [
        _ROOT / "utils" / "portfolio_session" / "constructive_tape_override.py",
        _ROOT / "utils" / "si_recommendation_queue.py",
        _ROOT / "config" / "si_fix_registry.json",
    ],
}


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def deployed_path() -> Path:
    return _data_dir() / "si_deployed_fixes.json"


def load_deployed() -> dict[str, Any]:
    p = deployed_path()
    if not p.exists():
        return {"fixes": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"fixes": {}}
    except Exception:
        return {"fixes": {}}


def save_deployed(doc: dict[str, Any]) -> None:
    deployed_path().parent.mkdir(parents=True, exist_ok=True)
    doc.setdefault("system_tz", system_tz_name())
    deployed_path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def code_guard_present_in_repo(code: str) -> bool:
    from utils.si_recommendation_queue import load_fix_registry

    reg = load_fix_registry().get("fixes") or {}
    meta = reg.get(code) if isinstance(reg, dict) else None
    if not isinstance(meta, dict):
        return False
    markers = [str(m) for m in (meta.get("mitigation_markers") or []) if m]
    if not markers:
        return False
    paths = _GUARD_SOURCES.get(code) or list(_ROOT.glob("agents/**/*.py"))
    blob = ""
    for path in paths:
        if path.is_file():
            try:
                blob += path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    if not blob:
        return False
    return all(m in blob for m in markers)


def _estimate_deploy_time(code: str) -> str:
    paths = _GUARD_SOURCES.get(code) or []
    tz = now().tzinfo
    mtimes: list[datetime] = []
    for path in paths:
        if path.is_file():
            try:
                mtimes.append(datetime.fromtimestamp(path.stat().st_mtime, tz=tz))
            except OSError:
                pass
    when = min(mtimes) if mtimes else now()
    return when.isoformat()


def _deployed_at(entry: dict[str, Any]) -> str | None:
    raw = entry.get("deployed_at") or entry.get("deployed_at_utc")
    return str(raw) if raw else None


def sync_deployed_from_registry() -> list[str]:
    """Record deployed_at for registry code guards present in source (idempotent)."""
    from utils.si_recommendation_queue import load_fix_registry

    doc = load_deployed()
    fixes = doc.setdefault("fixes", {})
    recorded: list[str] = []
    reg = load_fix_registry().get("fixes") or {}
    now_ts = now_iso()
    for code in reg if isinstance(reg, dict) else {}:
        meta = reg.get(code)
        if not isinstance(meta, dict) or meta.get("kind") != "code_guard":
            continue
        if not code_guard_present_in_repo(code):
            continue
        entry = fixes.get(code) if isinstance(fixes.get(code), dict) else {}
        if not _deployed_at(entry):
            ts = _estimate_deploy_time(code)
            entry["deployed_at"] = ts
            entry["deployed_at_utc"] = ts
            entry["verified"] = True
            fixes[code] = entry
            recorded.append(code)
        else:
            entry.setdefault("deployed_at", _deployed_at(entry))
            entry.setdefault("deployed_at_utc", entry.get("deployed_at"))
            fixes[code] = entry
    doc["updated_at"] = now_ts
    doc["system_tz"] = system_tz_name()
    save_deployed(doc)
    return recorded


def is_deployed(code: str) -> bool:
    entry = (load_deployed().get("fixes") or {}).get(code)
    if isinstance(entry, dict) and _deployed_at(entry):
        return True
    return code_guard_present_in_repo(code)


def deployed_at(code: str) -> datetime | None:
    entry = (load_deployed().get("fixes") or {}).get(code)
    if not isinstance(entry, dict):
        return None
    return parse_iso(_deployed_at(entry))
