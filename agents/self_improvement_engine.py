#!/usr/bin/env python3
"""
Tier-1 safe self-improvement: bounded parameter proposals, audit log, human approval, velocity limits.

Does NOT loosen immutable risk rails (pre-trade gate, position caps in gate remain authoritative).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

try:
    from utils.env_load import load_fortress_dotenv

    load_fortress_dotenv(_ROOT)
except Exception:
    pass

from utils.improvement_governance import AUTO_APPROVE_CRITERIA  # noqa: E402
from utils.tunable_overrides import (  # noqa: E402
    clear_overrides,
    get_confidence_threshold,
    get_decision_interval_seconds,
    get_position_size_pct,
    get_rsi_entry_threshold_int,
    load_overrides,
    save_overrides,
)


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def _log_path() -> Path:
    _data_dir().mkdir(parents=True, exist_ok=True)
    return _data_dir() / "self_improvement_log.jsonl"


def _pending_path() -> Path:
    return _data_dir() / "self_improvement_pending.json"


def _state_path() -> Path:
    return _data_dir() / "self_improvement_state.json"


IMMUTABLE_CONSTRAINTS: dict[str, Any] = {
    "max_position_size_pct": 0.03,
    "max_total_exposure_pct": 0.25,
    "require_pre_trade_gate": True,
    "require_stop_loss": True,
    "min_win_rate_target": 0.80,
}

TUNABLE_BOUNDS: dict[str, dict[str, float]] = {
    "confidence_threshold": {"min": 0.6, "max": 0.95},
    "decision_interval": {"min": 120, "max": 1800},
    "rsi_entry_threshold": {"min": 35, "max": 50},
    "position_size_pct": {"min": 0.02, "max": 0.03},
}

MAX_CHANGES_PER_WEEK = 1
MAX_CHANGES_PER_MONTH = 3

AUTO_APPROVE_MIN_WIN_RATE_DELTA = 0.05
SAFE_WIN_RATE_FLOOR = 0.75


@dataclass
class PerformanceSnapshot:
    decision_count: int = 0
    win_rate: float | None = None
    avg_confidence: float | None = None
    execution_rate: float | None = None
    missed_count: int = 0
    closed_trades_wins: int = 0
    closed_trades_total: int = 0


class SelfImprovementEngine:
    """Proposes single-parameter changes within bounds; never edits immutable constraints."""

    def __init__(self) -> None:
        self._ensure_files()

    def _ensure_files(self) -> None:
        _data_dir().mkdir(parents=True, exist_ok=True)
        if not _state_path().exists():
            _state_path().write_text(
                json.dumps({"halted": False, "halt_reason": None}, indent=2),
                encoding="utf-8",
            )

    def current_tunable_snapshot(self) -> dict[str, Any]:
        return {
            "confidence_threshold": get_confidence_threshold(),
            "decision_interval": float(
                get_decision_interval_seconds(None)
                or float(os.environ.get("FORTRESS_AI_LOOP_SECONDS", "300"))
            ),
            "rsi_entry_threshold": get_rsi_entry_threshold_int(),
            "position_size_pct": get_position_size_pct(),
        }

    def load_recent_performance(self, max_rows: int = 80) -> PerformanceSnapshot:
        p = _data_dir() / "ai_decisions.jsonl"
        rows: list[dict] = []
        if p.exists():
            try:
                raw = p.read_bytes()
                if len(raw) > 256_000:
                    raw = raw[-256_000:]
                for line in raw.decode("utf-8", errors="replace").strip().split("\n")[-max_rows:]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

        snap = PerformanceSnapshot(decision_count=len(rows))
        confs: list[float] = []
        exec_n = 0
        missed = 0
        for r in rows:
            d = r.get("decision")
            if not isinstance(d, dict):
                continue
            c = d.get("confidence")
            if c is not None:
                try:
                    confs.append(float(c))
                except (TypeError, ValueError):
                    pass
            act = r.get("act") if isinstance(r.get("act"), dict) else {}
            if act.get("executed"):
                exec_n += 1
            if d.get("action") == "wait" and float(d.get("confidence") or 0) >= 0.65:
                missed += 1

        if confs:
            snap.avg_confidence = sum(confs) / len(confs)
        if rows:
            snap.execution_rate = exec_n / len(rows)
        snap.missed_count = min(missed, len(rows))

        from utils.decision_log_metrics import trade_pnls_for_enter_executions, win_rate_from_pnls

        tp = trade_pnls_for_enter_executions([r for r in rows if not r.get("error")])
        if tp:
            wr = win_rate_from_pnls(tp)
            snap.win_rate = wr
            snap.closed_trades_total = len(tp)
            snap.closed_trades_wins = sum(1 for p in tp if p > 0)
        else:
            snap.win_rate = None
            snap.closed_trades_total = 0
            snap.closed_trades_wins = 0
        return snap

    def _velocity_ok(self) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=31)
        week_n = month_n = 0
        if _log_path().exists():
            try:
                for line in _log_path().read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if o.get("decision") not in ("approved_human", "auto_approved"):
                        continue
                    ts = o.get("timestamp") or ""
                    try:
                        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        continue
                    if t >= week_ago:
                        week_n += 1
                    if t >= month_ago:
                        month_n += 1
            except Exception:
                pass
        if week_n >= MAX_CHANGES_PER_WEEK:
            return False, f"weekly_limit:{week_n}>={MAX_CHANGES_PER_WEEK}"
        if month_n >= MAX_CHANGES_PER_MONTH:
            return False, f"monthly_limit:{month_n}>={MAX_CHANGES_PER_MONTH}"
        return True, "ok"

    def validate_proposal_json(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        param = str(raw.get("parameter") or "").strip()
        if param not in TUNABLE_BOUNDS:
            return None
        proposed = raw.get("proposed_value")
        try:
            pv = float(proposed)
        except (TypeError, ValueError):
            return None
        if param == "rsi_entry_threshold":
            pv = float(int(round(pv)))
        elif param == "position_size_pct":
            pv = round(float(pv), 4)
        b = TUNABLE_BOUNDS[param]
        if pv < b["min"] or pv > b["max"]:
            return None
        cur = self.current_tunable_snapshot().get(param)
        if cur is not None and abs(float(cur) - pv) < 1e-9:
            return None
        return {
            "parameter": param,
            "current_value": float(cur) if cur is not None else None,
            "proposed_value": pv,
            "reasoning": str(raw.get("reasoning") or "")[:4000],
            "expected_impact": str(raw.get("expected_impact") or "")[:2000],
            "risks": str(raw.get("risks") or "")[:2000],
        }

    def propose_via_llm(self) -> dict[str, Any]:
        from agents.unified_ai_agent import call_deepseek, _parse_llm_json

        perf = self.load_recent_performance()
        tunable = self.current_tunable_snapshot()
        prompt = f"""You are a conservative trading-system tuner. Output ONE JSON object only (no markdown).

IMMUTABLE (never propose changing): {json.dumps(IMMUTABLE_CONSTRAINTS)}
TUNABLE_BOUNDS: {json.dumps(TUNABLE_BOUNDS)}
CURRENT_TUNABLE: {json.dumps(tunable)}

Recent stats (best-effort from logs):
- decisions_sampled: {perf.decision_count}
- avg_confidence: {perf.avg_confidence}
- execution_rate: {perf.execution_rate}
- missed_proxy_count: {perf.missed_count}

Propose exactly ONE parameter change within bounds to improve opportunity capture OR stability.
Respond with JSON:
{{"parameter":"confidence_threshold|decision_interval|rsi_entry_threshold|position_size_pct","current_value":number,"proposed_value":number,"reasoning":"...","expected_impact":"...","risks":"..."}}
"""
        text, usage = call_deepseek(prompt, max_out_tokens=700)
        raw = _parse_llm_json(text)
        val = self.validate_proposal_json(raw)
        if not val:
            raise ValueError("invalid_or_out_of_bounds_proposal")
        return {"proposal": val, "usage": usage}

    def propose_heuristic(self) -> dict[str, Any]:
        """Fallback when LLM unavailable — tiny nudge toward mid-bound if at edge."""
        tunable = self.current_tunable_snapshot()
        # Nudge confidence down 0.02 if avg confidence high and many waits — conservative demo
        ct = float(tunable["confidence_threshold"])
        mid = (TUNABLE_BOUNDS["confidence_threshold"]["min"] + TUNABLE_BOUNDS["confidence_threshold"]["max"]) / 2
        new_ct = max(TUNABLE_BOUNDS["confidence_threshold"]["min"], min(TUNABLE_BOUNDS["confidence_threshold"]["max"], ct - 0.02))
        if abs(new_ct - ct) < 1e-6:
            new_ct = min(TUNABLE_BOUNDS["confidence_threshold"]["max"], ct + 0.02)
        raw = {
            "parameter": "confidence_threshold",
            "current_value": ct,
            "proposed_value": round(new_ct, 4),
            "reasoning": "Heuristic: explore threshold within bounds (no LLM).",
            "expected_impact": "May change execution vs wait mix.",
            "risks": "Could increase churn if lowered too far.",
        }
        val = self.validate_proposal_json(raw)
        if not val:
            raise ValueError("heuristic_invalid")
        return {"proposal": val, "usage": {}}

    def analyze_performance_and_propose_change(self) -> dict[str, Any]:
        from utils.improvement_governance import ImprovementGovernance, determine_governance_tier
        from utils.shadow_tester import ShadowTester

        st = json.loads(_state_path().read_text(encoding="utf-8"))
        if st.get("halted"):
            raise RuntimeError(f"self_improvement_halted:{st.get('halt_reason')}")

        ok, reason = self._velocity_ok()
        if not ok:
            raise RuntimeError(reason)

        api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if api_key:
            bundle = self.propose_via_llm()
        else:
            bundle = self.propose_heuristic()

        proposal = bundle["proposal"]
        pid = str(uuid.uuid4())
        shadow_engine = self.shadow_test_proposal(proposal)
        tester = ShadowTester()
        gov_shadow = tester.test_parameter_change(
            str(proposal.get("parameter")),
            float(proposal.get("proposed_value") or 0),
            days=int(AUTO_APPROVE_CRITERIA.get("min_shadow_days", 3)),
        )
        shadow = {**shadow_engine, **gov_shadow}

        tier = determine_governance_tier(str(proposal.get("parameter")))
        if tier == "tier_3_blocked":
            raise RuntimeError("tier_3_blocked_immutable_parameter")

        gov = ImprovementGovernance()
        gd = gov.process_proposal(proposal=proposal, proposal_id=pid, shadow_results=shadow, engine_rec=bundle)

        rec = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "proposal_id": pid,
            "proposal": proposal,
            "shadow_test": shadow,
            "governance": gd,
            "decision": gd.get("decision"),
            "outcome": "pending",
        }

        gdec = gd.get("decision")
        if gdec == "auto_approved":
            rec["outcome"] = "applied"
        elif gdec == "pending_veto_window":
            rec["outcome"] = "pending_veto"
            rec["veto_deadline"] = gd.get("veto_deadline")
        elif gdec in ("escalated_pending_human", "requires_explicit_approval"):
            rec["decision"] = "pending_human"
            rec["outcome"] = "pending"
            _pending_path().write_text(json.dumps({"id": pid, **rec}, indent=2, default=str), encoding="utf-8")
        elif gdec == "blocked_tier_3":
            raise RuntimeError("tier_3_blocked")

        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")

        return rec

    def shadow_test_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """Proxy shadow stats — full replay not implemented; compares confidence distribution."""
        rows: list[dict] = []
        p = _data_dir() / "ai_decisions.jsonl"
        if p.exists():
            try:
                raw = p.read_bytes()
                if len(raw) > 200_000:
                    raw = raw[-200_000:]
                for line in raw.decode("utf-8", errors="replace").split("\n")[-120:]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

        param = proposal.get("parameter")
        new_val = float(proposal.get("proposed_value") or 0)
        flipped = 0
        if param == "confidence_threshold" and rows:
            for r in rows:
                d = r.get("decision")
                if not isinstance(d, dict):
                    continue
                try:
                    c = float(d.get("confidence") or 0)
                except (TypeError, ValueError):
                    continue
                act = r.get("act") if isinstance(r.get("act"), dict) else {}
                old_exec = c >= get_confidence_threshold()
                new_exec = c >= new_val
                if old_exec != new_exec:
                    flipped += 1

        return {
            "duration_days": 0,
            "mode": "proxy_sample",
            "sample_decisions": len(rows),
            "threshold_cross_flips": flipped,
            "win_rate_delta": None,
            "missed_opportunities_delta": None,
            "max_drawdown_delta": None,
            "note": "Shadow replay is proxy-only until closed-trade PnL history exists.",
        }

    def _apply_proposal(self, proposal: dict[str, Any], change_id: str) -> None:
        param = proposal["parameter"]
        val = float(proposal["proposed_value"])
        cur = load_overrides()
        cur[param] = val
        cur["_meta"] = {
            "last_change_id": change_id,
            "applied_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        save_overrides(cur)

    def approve_pending(self, proposal_id: str | None = None) -> dict[str, Any]:
        if not _pending_path().exists():
            raise FileNotFoundError("no_pending_proposal")
        pending = json.loads(_pending_path().read_text(encoding="utf-8"))
        if proposal_id and pending.get("proposal_id") != proposal_id:
            raise ValueError("proposal_id_mismatch")
        prop = pending.get("proposal") or {}
        pid = pending.get("proposal_id") or str(uuid.uuid4())
        self._apply_proposal(prop, pid)
        rec = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "proposal_id": pid,
            "proposal": prop,
            "decision": "approved_human",
            "outcome": "applied",
        }
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        try:
            _pending_path().unlink()
        except OSError:
            pass
        return rec

    def reject_pending(self, reason: str = "") -> dict[str, Any]:
        if not _pending_path().exists():
            raise FileNotFoundError("no_pending_proposal")
        pending = json.loads(_pending_path().read_text(encoding="utf-8"))
        rec = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "proposal_id": pending.get("proposal_id"),
            "decision": "rejected_human",
            "reason": reason[:2000],
            "outcome": "rejected",
        }
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        try:
            _pending_path().unlink()
        except OSError:
            pass
        return rec

    def revert_last_overrides(self, reason: str = "") -> dict[str, Any]:
        clear_overrides()
        rec = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision": "reverted",
            "reason": reason[:2000],
            "outcome": "overrides_cleared",
        }
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def halt(self, reason: str) -> None:
        _state_path().write_text(
            json.dumps({"halted": True, "halt_reason": reason[:2000]}, indent=2),
            encoding="utf-8",
        )

    def resume(self) -> None:
        _state_path().write_text(json.dumps({"halted": False, "halt_reason": None}, indent=2), encoding="utf-8")

    def monitor_and_revert_if_needed(self) -> dict[str, Any] | None:
        """If measured win rate exists and drops below floor, revert + halt."""
        perf = self.load_recent_performance()
        if perf.win_rate is None:
            return None
        if perf.win_rate < SAFE_WIN_RATE_FLOOR:
            self.revert_last_overrides(reason=f"auto_win_rate_{perf.win_rate}")
            self.halt(f"performance_below_{SAFE_WIN_RATE_FLOOR}")
            return {"reverted": True, "halted": True, "win_rate": perf.win_rate}
        return None

    def status_dict(self) -> dict[str, Any]:
        pending = None
        if _pending_path().exists():
            try:
                pending = json.loads(_pending_path().read_text(encoding="utf-8"))
            except Exception:
                pending = {"error": "read_failed"}
        st = json.loads(_state_path().read_text(encoding="utf-8"))
        proposals = self.list_recent_log(40)
        approved = sum(1 for x in proposals if x.get("decision") in ("approved_human", "auto_approved"))
        total_lines = self.count_log_lines()
        return {
            "immutable": IMMUTABLE_CONSTRAINTS,
            "tunable_bounds": TUNABLE_BOUNDS,
            "current": self.current_tunable_snapshot(),
            "active_overrides": load_overrides(),
            "pending": pending,
            "state": st,
            "velocity": {"max_per_week": MAX_CHANGES_PER_WEEK, "max_per_month": MAX_CHANGES_PER_MONTH},
            "counts": {
                "recent_log_lines": len(proposals),
                "approved_total_sampled": approved,
                "total_log_entries": total_lines,
            },
        }

    def list_recent_log(self, n: int = 50) -> list[dict[str, Any]]:
        if not _log_path().exists():
            return []
        lines = _log_path().read_text(encoding="utf-8").splitlines()[-n:]
        out: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def count_log_lines(self) -> int:
        if not _log_path().exists():
            return 0
        try:
            return sum(1 for line in _log_path().read_text(encoding="utf-8").splitlines() if line.strip())
        except Exception:
            return 0


def get_engine() -> SelfImprovementEngine:
    return SelfImprovementEngine()
