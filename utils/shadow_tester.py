"""
Shadow-mode comparison for proposed parameter changes (proxy / replay-lite).

Full broker replay is not implemented; deltas use historical decision rows and
parameter-specific heuristics. Win rate remains None until fills exist in logs.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from utils.decision_log_metrics import (
    max_drawdown_fraction,
    trade_pnls_for_enter_executions,
    trade_pnls_if_confidence_ge,
    win_rate_from_pnls,
)
from utils.tunable_overrides import get_confidence_threshold


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent / "data"


def _parse_ts(row: dict[str, Any]) -> datetime | None:
    ts_raw = row.get("ts") or row.get("timestamp") or ""
    if not ts_raw:
        return None
    try:
        return datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except Exception:
        return None


class ShadowTester:
    def load_decisions_last_n_days(self, days: int, *, max_lines: int = 800) -> list[dict[str, Any]]:
        p = _data_dir() / "ai_decisions.jsonl"
        if not p.exists():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        recent: list[dict[str, Any]] = []
        try:
            raw = p.read_bytes()
            if len(raw) > 400_000:
                raw = raw[-400_000:]
            for line in raw.decode("utf-8", errors="replace").split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = _parse_ts(row)
                if t is None or t < cutoff:
                    continue
                recent.append(row)
        except Exception:
            pass
        return recent[-max_lines:]

    def compute_actual_results(self, decisions: list[dict[str, Any]]) -> dict[str, Any]:
        if not decisions:
            return {
                "execution_rate": 0.0,
                "win_rate": None,
                "avg_confidence": None,
                "max_drawdown": None,
            }
        confs: list[float] = []
        exec_n = 0
        for drow in decisions:
            if drow.get("error"):
                continue
            d = drow.get("decision")
            if not isinstance(d, dict):
                continue
            if d.get("confidence") is not None:
                try:
                    confs.append(float(d["confidence"]))
                except (TypeError, ValueError):
                    pass
            act = drow.get("act") if isinstance(drow.get("act"), dict) else {}
            if act.get("executed"):
                exec_n += 1
        actions = [
            str((drow.get("decision") or {}).get("action") or "")
            for drow in decisions
            if isinstance(drow.get("decision"), dict)
        ]
        n = max(len(actions), 1)
        er = sum(1 for a in actions if a in ("enter_position", "exit_position")) / n
        clean = [d for d in decisions if not d.get("error")]
        tp = trade_pnls_for_enter_executions(clean)
        wr = win_rate_from_pnls(tp) if tp else None
        mdd = max_drawdown_fraction(tp) if len(tp) >= 2 else None
        return {
            "execution_rate": er,
            "win_rate": wr,
            "avg_confidence": sum(confs) / len(confs) if confs else None,
            "max_drawdown": mdd,
            "pnl_sample_size": len(tp),
        }

    def simulate_with_new_param(
        self, decisions: list[dict[str, Any]], parameter: str, new_value: float
    ) -> dict[str, Any]:
        if parameter == "confidence_threshold":
            nv = float(new_value)
            flipped = 0
            exec_sim = 0
            confs: list[float] = []
            for drow in decisions:
                d = drow.get("decision")
                if not isinstance(d, dict):
                    continue
                try:
                    c = float(d.get("confidence") or 0)
                except (TypeError, ValueError):
                    continue
                confs.append(c)
                old_th = get_confidence_threshold()
                old_exec = c >= old_th
                new_exec = c >= nv
                if old_exec != new_exec:
                    flipped += 1
                if new_exec and (d.get("action") or "") in ("enter_position", "exit_position"):
                    exec_sim += 1
            n = max(len(decisions), 1)
            clean = [d for d in decisions if not d.get("error")]
            tp_sim = trade_pnls_if_confidence_ge(clean, nv)
            wr_sim = win_rate_from_pnls(tp_sim) if tp_sim else None
            mdd_sim = max_drawdown_fraction(tp_sim) if len(tp_sim) >= 2 else None
            return {
                "execution_rate": min(1.0, exec_sim / n),
                "win_rate": wr_sim,
                "avg_confidence": sum(confs) / len(confs) if confs else None,
                "max_drawdown": mdd_sim,
                "threshold_cross_flips": flipped,
                "pnl_sample_size": len(tp_sim),
            }

        if parameter == "decision_interval":
            return {
                "execution_rate": self.compute_actual_results(decisions)["execution_rate"],
                "win_rate": None,
                "avg_confidence": None,
                "max_drawdown": None,
                "note": "interval does not change past decisions in proxy mode",
            }

        # rsi / position_size — neutral proxy
        base = self.compute_actual_results(decisions)
        return {
            "execution_rate": base["execution_rate"],
            "win_rate": None,
            "avg_confidence": base["avg_confidence"],
            "max_drawdown": None,
        }

    def test_parameter_change(self, parameter: str, new_value: float, days: int = 3) -> dict[str, Any]:
        decisions = self.load_decisions_last_n_days(days)
        if len(decisions) < 3:
            return {
                "error": "insufficient_data",
                "decision_count": len(decisions),
                "test_period_days": days,
                "parameter_tested": parameter,
                "tested_value": new_value,
            }

        simulated = self.simulate_with_new_param(decisions, parameter, new_value)
        actual = self.compute_actual_results(decisions)

        def _d(a: float | None, b: float | None) -> float | None:
            if a is None or b is None:
                return None
            return float(a) - float(b)

        wr_d = _d(simulated.get("win_rate"), actual.get("win_rate"))
        dd_d = _d(simulated.get("max_drawdown"), actual.get("max_drawdown"))
        ac_d = _d(simulated.get("avg_confidence"), actual.get("avg_confidence"))
        er_d = _d(simulated.get("execution_rate"), actual.get("execution_rate"))

        return {
            "decision_count": len(decisions),
            "test_period_days": days,
            "parameter_tested": parameter,
            "tested_value": new_value,
            "win_rate_delta": wr_d,
            "avg_confidence_delta": ac_d,
            "execution_rate_delta": er_d,
            "max_drawdown_delta": dd_d,
            "threshold_cross_flips": simulated.get("threshold_cross_flips", 0),
            "actual": actual,
            "simulated": simulated,
        }
