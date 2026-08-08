"""SI broker hygiene — act on broker_error spikes (pause re-entry; log hygiene).

Never loosens pre_trade_gate. Only pauses new entries on symbols with repeated rejects.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Any

from utils.si_decision_scan import block_reason, item_symbol, iter_session_decisions
from utils.system_time import now, now_iso

log = logging.getLogger(__name__)
_MARKER = "si_broker_hygiene"


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_BROKER_HYGIENE", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _threshold() -> int:
    try:
        return max(2, int(os.environ.get("FORTRESS_SI_BROKER_ERROR_SYMBOL_MIN", "2") or 2))
    except ValueError:
        return 2


def collect_broker_error_symbols(component: str) -> dict[str, Any]:
    """Count broker_error blocks per symbol for current ET session."""
    today = now().date().isoformat()
    sub = "skim_swarm" if "infra" not in component else "infra_swarm"
    if component in ("skim_swarm", "infra_swarm"):
        sub = component
    path = _data_dir() / sub / "decisions.jsonl"
    ctr: Counter[str] = Counter()
    samples: list[str] = []
    for _wave, item in iter_session_decisions(path, today=today):
        br = block_reason(item)
        if br != "broker_error" and not br.startswith("broker_error"):
            continue
        sym = item_symbol(item)
        if not sym:
            continue
        ctr[sym] += 1
        if len(samples) < 12:
            detail = str((item.get("act") or {}).get("detail") or br)[:120]
            samples.append(f"{sym}:{detail}")
    return {
        "ok": True,
        "component": component,
        "session_date_et": today,
        "counts": dict(ctr),
        "total": int(sum(ctr.values())),
        "samples": samples,
        "marker": _MARKER,
    }


def _side_conflict_detail(detail: str) -> bool:
    d = (detail or "").lower()
    needles = (
        "side",
        "403",
        "existing order",
        "open sell",
        "open buy",
        "opposite",
        "conflict",
        "would take",
        "wash",
    )
    return any(n in d for n in needles)


def clear_broker_side_conflicts(component: str, *, samples: list[str] | None = None) -> dict[str, Any]:
    """Cancel open orders on symbols with side-conflict / open-order broker rejects."""
    if str(os.environ.get("FORTRESS_SI_BROKER_CANCEL_OPEN", "1")).strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"skipped": "cancel_open_disabled", "cancelled": []}

    summary = collect_broker_error_symbols(component)
    raw_samples = list(samples if samples is not None else (summary.get("samples") or []))
    targets: set[str] = set()
    for sample in raw_samples:
        s = str(sample or "")
        sym = s.split(":", 1)[0].strip().upper()
        detail = s.split(":", 1)[1] if ":" in s else s
        if sym and _side_conflict_detail(detail):
            targets.add(sym)
    # Also include high-count offenders when samples look like order rejects
    thr = _threshold()
    for sym, n in (summary.get("counts") or {}).items():
        if int(n) >= thr and any(sym in str(x) and _side_conflict_detail(str(x)) for x in raw_samples):
            targets.add(str(sym).upper())

    cancelled: list[str] = []
    if not targets:
        return {"ok": True, "cancelled": [], "targets": [], "marker": _MARKER}

    try:
        from utils.alpaca_execution import cancel_open_orders
    except Exception as e:
        return {"error": f"cancel_import:{e}"[:120], "cancelled": [], "marker": _MARKER}

    for sym in sorted(targets):
        try:
            n = int(cancel_open_orders(sym) or 0)
            if n > 0:
                cancelled.append(f"{sym}:cancelled_open={n}")
            else:
                cancelled.append(f"{sym}:cancelled_open=0")
        except Exception as e:
            cancelled.append(f"{sym}:error={str(e)[:40]}")
    if cancelled:
        log.info("%s side_conflict_cancel component=%s %s", _MARKER, component, cancelled[:8])
    return {
        "ok": True,
        "cancelled": cancelled,
        "targets": sorted(targets),
        "marker": _MARKER,
    }


def apply_broker_error_symbol_brakes(component: str) -> dict[str, Any]:
    """Pause new entries on symbols with ≥N broker_error blocks this session."""
    if not _enabled():
        return {"skipped": "broker_hygiene_disabled", "marker": _MARKER, "brakes": []}
    if component not in ("skim_swarm", "infra_swarm"):
        return {"skipped": "bad_component", "marker": _MARKER, "brakes": []}

    summary = collect_broker_error_symbols(component)
    thr = _threshold()
    side_clear = clear_broker_side_conflicts(component, samples=list(summary.get("samples") or []))
    offenders = sorted(
        (s for s, n in (summary.get("counts") or {}).items() if int(n) >= thr),
        key=lambda s: -int((summary.get("counts") or {}).get(s) or 0),
    )
    if not offenders:
        return {
            "ok": True,
            "brakes": [],
            "newly_applied": [],
            "side_conflict_clear": side_clear,
            "summary": summary,
            "marker": _MARKER,
        }

    learned_dir = _data_dir() / component / "learned"
    learned_dir.mkdir(parents=True, exist_ok=True)
    today = str(summary.get("session_date_et") or now().date().isoformat())
    newly: list[str] = []
    brakes: list[str] = []

    for sym in offenders:
        n = int((summary.get("counts") or {}).get(sym) or 0)
        f = learned_dir / f"{sym.lower().replace('.', '_')}.json"
        doc: dict[str, Any] = {}
        if f.is_file():
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                doc = {}
        if not isinstance(doc, dict):
            doc = {}
        params = doc.setdefault("params", {})
        already = bool(params.get("pause_entries"))
        params["pause_entries"] = True
        params["pause_long"] = True
        params["pause_short"] = True
        notes = list(doc.get("si_notes") or [])
        notes.append(f"broker_error_brake n={n} thr={thr} marker={_MARKER}")
        doc["si_notes"] = notes[-8:]
        doc["session_date_et"] = today
        doc["si_broker_hygiene"] = {
            "ts": now_iso(),
            "broker_error_count": n,
            "threshold": thr,
            "marker": _MARKER,
        }
        f.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        msg = f"{sym}:broker_error_pause n={n} marker={_MARKER}"
        brakes.append(msg)
        if not already:
            newly.append(msg)

    # Persist session hygiene snapshot for operator / SI model.
    snap = _data_dir() / "si_capability" / "broker_hygiene.json"
    snap.parent.mkdir(parents=True, exist_ok=True)
    try:
        prev = {}
        if snap.is_file():
            prev = json.loads(snap.read_text(encoding="utf-8"))
        if not isinstance(prev, dict):
            prev = {}
        prev[component] = {
            "ts": now_iso(),
            "session_date_et": today,
            "counts": summary.get("counts"),
            "brakes": brakes,
            "samples": summary.get("samples"),
            "marker": _MARKER,
        }
        snap.write_text(json.dumps(prev, indent=2), encoding="utf-8")
    except Exception:
        pass

    if newly:
        try:
            from utils.si_capability_review import collect_metrics
            from utils.si_intervention_log import record_intervention

            record_intervention(
                component=component,
                action="broker_error_hygiene",
                metrics_snapshot=collect_metrics(),
                detail={
                    "marker": _MARKER,
                    "brakes": newly[:12],
                    "all_brakes": brakes[:12],
                    "counts": summary.get("counts"),
                    "side_conflict_clear": side_clear,
                },
                scoreable=True,
            )
        except Exception as e:
            log.warning("broker_hygiene_record_failed: %s", e)

    return {
        "ok": True,
        "brakes": brakes,
        "newly_applied": newly,
        "threshold": thr,
        "side_conflict_clear": side_clear,
        "summary": summary,
        "marker": _MARKER,
    }


def run_broker_hygiene_cycle() -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "marker": _MARKER}
    for component in ("skim_swarm", "infra_swarm"):
        out[component] = apply_broker_error_symbol_brakes(component)
    return out


__all__ = [
    "apply_broker_error_symbol_brakes",
    "clear_broker_side_conflicts",
    "collect_broker_error_symbols",
    "run_broker_hygiene_cycle",
]
