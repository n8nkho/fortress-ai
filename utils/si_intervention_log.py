"""SI intervention attribution — measure whether actions improved outcomes."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from utils.system_time import now, now_iso

# Heartbeat / no-op / exhausted actions must not count for success-rate scoring.
_NO_OP_ACTIONS = frozenset(
    {
        "swarm_session_normal",
        "edge_autofix_exhausted",
        "si_meta_heartbeat",
    }
)
_PROTECTIVE_ACTIONS = frozenset(
    {
        "symbol_session_brake",
        "swarm_session_tight",
        "swarm_session_churn",
        "edge_autofix",
        "deep_lag_wait",
        "denylist_thaw",
        "infra_strong_tape_soft",
        "denylist_audit",
        "broker_error_hygiene",
    }
)
_MIN_EXP_DELTA = 0.005
_MIN_PAY_DELTA = 0.02


def _is_protective(action: str) -> bool:
    a = str(action or "")
    if a in _PROTECTIVE_ACTIONS:
        return True
    return a.startswith("gap_dispatch:")


def _held_or_improved(
    *,
    before: float,
    after: float,
    min_delta: float,
    protective: bool,
) -> bool:
    if after > before + min_delta:
        return True
    if protective and after >= before - min_delta:
        return True
    return False


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def intervention_log_path() -> Path:
    return _data_dir() / "si_capability" / "interventions.jsonl"


def record_intervention(
    *,
    component: str,
    action: str,
    metrics_snapshot: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    scoreable: bool = True,
) -> None:
    p = intervention_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": now_iso(),
        "component": component,
        "action": action,
        "metrics_snapshot": metrics_snapshot or {},
        "detail": detail or {},
        "scoreable": bool(scoreable),
        "session_date_et": now().date().isoformat(),
        "markers": ["si_intervention_recorded"],
    }
    if scoreable:
        try:
            from utils.si_predictability import attach_prediction_to_intervention

            pred = attach_prediction_to_intervention(
                component=component,
                action=action,
                metrics_snapshot=metrics_snapshot,
                detail=detail,
            )
            row["prediction"] = {
                "id": pred.get("id"),
                "predicted_outcome": pred.get("predicted_outcome"),
                "predicted_delta_expectancy_usd": pred.get("predicted_delta_expectancy_usd"),
                "confidence": pred.get("confidence"),
                "action_family": pred.get("action_family"),
                "family_hit_rate": pred.get("family_hit_rate"),
                "model_n_updates": pred.get("model_n_updates"),
                "marker": "si_predictability",
            }
            markers = list(row["markers"])
            markers.append("si_predictability")
            row["markers"] = markers
        except Exception:
            pass
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _read_tail(path: Path, *, max_bytes: int = 256_000) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raw = raw[-max_bytes:]
    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
            if isinstance(doc, dict):
                out.append(doc)
        except Exception:
            continue
    return out


def _expectancy_usd(comp_metrics: dict[str, Any]) -> float | None:
    """Prefer rolling expectancy; fall back to session when rolling window is empty."""
    for key in ("rolling_expectancy_usd", "session_expectancy_usd"):
        val = comp_metrics.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _session_key(row: dict[str, Any]) -> str:
    return str(row.get("session_date_et") or str(row.get("ts") or "")[:10])


_POST_FORMAT_MARKERS = frozenset(
    {
        "si_intervention_recorded",
        "si_predictability",
        "gap_action_dispatch",
    }
)


def _is_post_format_scoreable(row: dict[str, Any]) -> bool:
    """True SI format: session_date_et + scoreable, excluding exhausted/no-op noise.

    If markers are present they must include an SI post-format marker (or a
    prediction payload). Bare session_date_et still counts so tests/legacy
    post-fix rows remain scoreable.
    """
    if row.get("scoreable") is False:
        return False
    if not row.get("session_date_et"):
        return False
    action = str(row.get("action") or "")
    if action in _NO_OP_ACTIONS:
        return False
    detail = row.get("detail") or {}
    if str(detail.get("marker") or "") == "edge_autofix_exhausted":
        return False
    if str(detail.get("skipped") or "") == "edge_autofix_exhausted":
        return False
    markers = row.get("markers")
    if isinstance(markers, list) and markers:
        tagged = {str(m) for m in markers}
        if tagged & _POST_FORMAT_MARKERS:
            return True
        if any(str(m).startswith("si_") for m in markers):
            return True
        if isinstance(row.get("prediction"), dict) and row.get("prediction"):
            return True
        return False
    return True


def intervention_success_rate(
    metrics: dict[str, Any],
    *,
    lookback: int = 24,
) -> float | None:
    """Fraction of distinct recent post-format interventions with material improvement.

    Prefer the current ET session so older snapshots are not scored against
    today's rolling expectancy (that mix was collapsing the headline rate).
    """
    rows = _read_tail(intervention_log_path())[-lookback:]
    if not rows:
        return None

    # Dedupe spam: one score slot per (component, action, session_date).
    # Only score post true-SI format rows (session_date_et set by record_intervention).
    # Legacy pre-fix spam (edge_autofix / swarm_session_tight without session_date_et)
    # must not poison intervention_success_rate.
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        if not _is_post_format_scoreable(row):
            continue
        comp = str(row.get("component") or "")
        if not comp or comp == "si_meta":
            continue
        action = str(row.get("action") or "")
        key = (comp, action, _session_key(row))
        deduped[key] = row  # keep latest in session

    today = now().date().isoformat()
    today_rows = [r for r in deduped.values() if _session_key(r) == today]
    pool = today_rows if today_rows else list(deduped.values())

    improved = 0
    scored = 0
    for row in pool:
        comp = str(row.get("component") or "")
        action = str(row.get("action") or "")
        protective = _is_protective(action)
        before = (row.get("metrics_snapshot") or {}).get(comp) or {}
        before_exp = _expectancy_usd(before)
        after_exp = _expectancy_usd(metrics.get(comp) or {})
        if before_exp is not None and after_exp is not None:
            scored += 1
            if _held_or_improved(
                before=before_exp,
                after=after_exp,
                min_delta=_MIN_EXP_DELTA,
                protective=protective,
            ):
                improved += 1
            continue
        before_pay = before.get("rolling_payoff_ratio")
        after_pay = (metrics.get(comp) or {}).get("rolling_payoff_ratio")
        if before_pay is None or after_pay is None:
            continue
        try:
            scored += 1
            if _held_or_improved(
                before=float(before_pay),
                after=float(after_pay),
                min_delta=_MIN_PAY_DELTA,
                protective=protective,
            ):
                improved += 1
        except (TypeError, ValueError):
            continue
    if not scored:
        return None
    return improved / scored
