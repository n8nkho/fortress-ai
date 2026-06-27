"""Reconcile Alpaca broker open positions vs swarm symbol-state ledger (operator loop)."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("operator_broker_reconcile")

_RECONCILE_MARKER = "reconcile_broker_ledger"
_DRIFT_MARKER = "operator_broker_open_drift"
_STALE_MARKER = "operator_broker_ledger_stale"
_ORPHAN_CLOSE_MARKER = "operator_broker_orphan_close"
_ADOPT_MARKER = "operator_broker_adopt"


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _reconcile_state_path() -> Path:
    return _data_dir() / "operator_status" / "reconcile_state.json"


def reconcile_enabled() -> bool:
    raw = os.environ.get("FORTRESS_RECONCILE_BROKER_LEDGER", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def reconcile_cooldown_sec() -> float:
    try:
        return max(10.0, float(os.environ.get("FORTRESS_RECONCILE_BROKER_LEDGER_COOLDOWN_SEC", "60") or 60))
    except (TypeError, ValueError):
        return 60.0


def _load_reconcile_state() -> dict[str, Any]:
    p = _reconcile_state_path()
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _save_reconcile_state(doc: dict[str, Any]) -> None:
    p = _reconcile_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _cooldown_elapsed() -> tuple[bool, float | None]:
    st = _load_reconcile_state()
    last = _parse_ts(st.get("last_run_ts"))
    if last is None:
        return True, None
    age = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds()
    return age >= reconcile_cooldown_sec(), age


def _swarm_state_dir(component: str) -> Path:
    if component == "skim_swarm":
        from utils.skim_swarm_config import swarm_data_dir

        return swarm_data_dir() / "state"
    from utils.infra_swarm_config import swarm_data_dir

    return swarm_data_dir() / "state"


def fetch_ledger_open_symbols() -> dict[str, str]:
    """Return symbol -> owning swarm component for open ledger positions."""
    from utils.swarm_runtime import held_position_symbols

    out: dict[str, str] = {}
    for component in ("skim_swarm", "infra_swarm"):
        for sym in sorted(held_position_symbols(_swarm_state_dir(component))):
            out.setdefault(sym, component)
    return out


def fetch_broker_positions() -> dict[str, dict[str, Any]]:
    """Return broker-held symbols with qty and side."""
    try:
        from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs

        key, sec = alpaca_credentials()
        if not key or not sec:
            return {}
        from alpaca.trading.client import TradingClient

        tc = TradingClient(key, sec, **alpaca_trading_client_kwargs())
        out: dict[str, dict[str, Any]] = {}
        for p in tc.get_all_positions():
            sym = str(getattr(p, "symbol", "") or "").upper()
            if not sym:
                continue
            try:
                qty = float(getattr(p, "qty", 0) or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty == 0:
                continue
            out[sym] = {
                "symbol": sym,
                "qty": int(abs(qty)),
                "side": "long" if qty > 0 else "short",
            }
        return out
    except Exception as e:
        logger.warning("%s broker_fetch_failed block_reason=broker_fetch_failed error=%s", _DRIFT_MARKER, e)
        return {}


def _mark_ledger_stale(symbol: str, component: str) -> dict[str, Any]:
    sym = str(symbol or "").upper()
    if component == "skim_swarm":
        from agents.skim_swarm.state import load_symbol_state, save_symbol_state
    else:
        from agents.infra_swarm.state import load_symbol_state, save_symbol_state

    st = load_symbol_state(sym)
    if str(st.get("side") or "flat").lower() not in ("long", "short"):
        return {"symbol": sym, "component": component, "skipped": "already_flat"}

    st["side"] = "flat"
    st["entry_price"] = None
    st["entry_ts"] = None
    st["peak_unrealized"] = 0.0
    st["last_action"] = _RECONCILE_MARKER
    st["last_block_reason"] = _STALE_MARKER
    st["reconcile_marker"] = _RECONCILE_MARKER
    save_symbol_state(st)
    logger.error(
        "%s ledger_stale symbol=%s component=%s block_reason=%s si_action=%s",
        _DRIFT_MARKER,
        sym,
        component,
        _STALE_MARKER,
        _RECONCILE_MARKER,
    )
    return {"symbol": sym, "component": component, "marked_stale": True}


def _try_adopt_tracked_orphan(symbol: str, broker_pos: dict[str, Any]) -> dict[str, Any] | None:
    """Restore swarm ledger when broker still holds a position we opened but state drifted to flat."""
    sym = str(symbol or "").upper()
    b_side = str(broker_pos.get("side") or "long").lower()
    b_qty = int(broker_pos.get("qty") or 0)
    if b_qty <= 0 or b_side not in ("long", "short"):
        return None

    entry_cutoff_hours = 120.0
    try:
        entry_cutoff_hours = max(
            24.0,
            float(os.environ.get("FORTRESS_RECONCILE_ADOPT_MAX_AGE_HOURS", "120") or 120),
        )
    except (TypeError, ValueError):
        pass

    for component in ("skim_swarm", "infra_swarm"):
        if component == "skim_swarm":
            from agents.skim_swarm.state import load_symbol_state, save_symbol_state
        else:
            from agents.infra_swarm.state import load_symbol_state, save_symbol_state

        st = load_symbol_state(sym)
        side = str(st.get("side") or "flat").lower()
        if side in ("long", "short"):
            return None
        entry_ts = st.get("entry_ts") or st.get("last_clip_ts")
        entry_px = st.get("entry_price")
        if not entry_ts or entry_px is None:
            continue
        parsed = _parse_ts(str(entry_ts))
        if parsed is None:
            continue
        age_h = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0
        if age_h > entry_cutoff_hours:
            continue

        st["side"] = b_side
        st["qty"] = b_qty
        st["reconcile_marker"] = _ADOPT_MARKER
        st["last_action"] = _RECONCILE_MARKER
        st["last_block_reason"] = _ADOPT_MARKER
        save_symbol_state(st)
        logger.warning(
            "%s adopt_orphan symbol=%s component=%s side=%s qty=%s entry_age_h=%.1f si_action=%s",
            _DRIFT_MARKER,
            sym,
            component,
            b_side,
            b_qty,
            age_h,
            _RECONCILE_MARKER,
        )
        return {
            "symbol": sym,
            "component": component,
            "adopted": True,
            "side": b_side,
            "qty": b_qty,
            "block_reason": _ADOPT_MARKER,
        }
    return None


def _submit_orphan_close(symbol: str, *, qty: int, side: str) -> dict[str, Any]:
    from utils.skim_swarm_config import dry_run

    sym = str(symbol or "").upper()
    if dry_run():
        logger.warning(
            "%s orphan_close_skipped symbol=%s qty=%s block_reason=dry_run_blocked si_action=%s",
            _DRIFT_MARKER,
            sym,
            qty,
            _RECONCILE_MARKER,
        )
        return {"symbol": sym, "executed": False, "block_reason": "dry_run_blocked"}

    try:
        from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs

        key, sec = alpaca_credentials()
        if not key or not sec:
            return {"symbol": sym, "executed": False, "block_reason": "alpaca_not_configured"}
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import MarketOrderRequest

        from utils.alpaca_execution import gate_exit_submission

        tc = TradingClient(key, sec, **alpaca_trading_client_kwargs())
        exit_side = "SELL" if side == "long" else "BUY"
        block = gate_exit_submission(sym, side=exit_side)
        if block:
            return {"symbol": sym, "executed": False, "block_reason": block.get("block_reason")}

        alpaca_side = "sell" if exit_side == "SELL" else "buy"
        order = tc.submit_order(
            MarketOrderRequest(symbol=sym, qty=int(qty), side=alpaca_side, time_in_force="day")
        )
        logger.warning(
            "%s orphan_close symbol=%s qty=%s side=%s order_id=%s block_reason=%s si_action=%s",
            _DRIFT_MARKER,
            sym,
            qty,
            exit_side,
            getattr(order, "id", None),
            _ORPHAN_CLOSE_MARKER,
            _RECONCILE_MARKER,
        )
        return {
            "symbol": sym,
            "executed": True,
            "qty": int(qty),
            "side": exit_side,
            "order_id": str(getattr(order, "id", "")),
            "block_reason": _ORPHAN_CLOSE_MARKER,
        }
    except Exception as e:
        logger.warning(
            "%s orphan_close_failed symbol=%s block_reason=broker_error error=%s",
            _DRIFT_MARKER,
            sym,
            e,
        )
        return {"symbol": sym, "executed": False, "block_reason": "broker_error", "error": str(e)[:120]}


def reconcile_broker_ledger(*, force: bool = False) -> dict[str, Any]:
    """Compare broker vs swarm ledger; close orphans and mark stale ledger rows."""
    if not reconcile_enabled() and not force:
        return {"ok": True, "skipped": "disabled", "si_action": _RECONCILE_MARKER}

    elapsed, age = _cooldown_elapsed()
    if not force and not elapsed:
        return {
            "ok": True,
            "skipped": "cooldown",
            "cooldown_sec": reconcile_cooldown_sec(),
            "age_sec": age,
            "si_action": _RECONCILE_MARKER,
        }

    broker = fetch_broker_positions()
    ledger = fetch_ledger_open_symbols()
    broker_syms = set(broker)
    ledger_syms = set(ledger)

    orphan_syms = sorted(broker_syms - ledger_syms)
    stale_syms = sorted(ledger_syms - broker_syms)

    orphan_actions: list[dict[str, Any]] = []
    adopted_syms: set[str] = set()
    for sym in orphan_syms:
        pos = broker[sym]
        adopted = _try_adopt_tracked_orphan(sym, pos)
        if adopted:
            orphan_actions.append(adopted)
            adopted_syms.add(sym)
            continue
        orphan_actions.append(_submit_orphan_close(sym, qty=int(pos["qty"]), side=str(pos["side"])))
    orphan_syms = [s for s in orphan_syms if s not in adopted_syms]

    stale_actions: list[dict[str, Any]] = []
    for sym in stale_syms:
        stale_actions.append(_mark_ledger_stale(sym, ledger[sym]))

    result = {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "broker_open": len(broker_syms),
        "ledger_open": len(ledger_syms),
        "orphan_symbols": orphan_syms,
        "stale_symbols": stale_syms,
        "orphan_actions": orphan_actions,
        "stale_actions": stale_actions,
        "mitigation_markers": [
            _DRIFT_MARKER,
            _RECONCILE_MARKER,
            _STALE_MARKER,
            _ORPHAN_CLOSE_MARKER,
            _ADOPT_MARKER,
        ],
        "si_action": _RECONCILE_MARKER,
        "block_reason": _DRIFT_MARKER if orphan_syms or stale_syms else None,
    }
    _save_reconcile_state({"last_run_ts": result["ts"], "last_result": result})
    if orphan_syms or stale_syms:
        logger.warning(
            "%s drift_detected broker=%d ledger=%d orphans=%s stale=%s",
            _DRIFT_MARKER,
            len(broker_syms),
            len(ledger_syms),
            orphan_syms,
            stale_syms,
        )
    return result


def maybe_reconcile_broker_ledger() -> dict[str, Any]:
    """Operator-loop entry: reconcile when enabled and cooldown elapsed."""
    return reconcile_broker_ledger(force=False)
