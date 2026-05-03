"""
Risk-based governance tiers (0–3) for autonomous parameter improvement.

Tier 0: auto-approve after shadow criteria (or proxy criteria when WR unknown).
Tier 1: notify + 24h veto window, then auto-apply unless vetoed.
Tier 2: explicit human approval only (handled via existing pending.json + dashboard).
Tier 3: immutable — proposals rejected at validation.

Prompt/strategy evolution stays in agents/prompt_evolution.py (Tier-2 prompt tier).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent

AUTO_APPROVE_CRITERIA: dict[str, Any] = {
    "min_shadow_days": 3,
    "min_win_rate_improvement": 0.05,
    "max_drawdown_increase": 0.02,
    "min_sample_size": 10,
}

TIER_0_PARAMS = frozenset({"confidence_threshold", "decision_interval"})
TIER_1_PARAMS = frozenset({"rsi_entry_threshold", "position_size_pct"})

# Logical Tier 3 — cannot be tuned via self-improvement engine
IMMUTABLE_PARAM_NAMES = frozenset(
    {
        "max_total_exposure_pct",
        "require_pre_trade_gate",
        "require_stop_loss",
        "min_win_rate_target",
        "max_position_size_pct",
        "weekly_cost_cap_usd",
    }
)


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else _ROOT / "data"


def proposals_path() -> Path:
    return _data_dir() / "improvement_proposals.jsonl"


def outcomes_path() -> Path:
    return _data_dir() / "improvement_outcomes.jsonl"


def governance_decisions_path() -> Path:
    return _data_dir() / "governance_decisions.jsonl"


def veto_pending_path() -> Path:
    return _data_dir() / "governance_veto_pending.json"


def tunable_params_snapshot_path() -> Path:
    return _data_dir() / "tunable_params.json"


def determine_governance_tier(parameter: str) -> str:
    p = str(parameter or "").strip()
    if p in IMMUTABLE_PARAM_NAMES:
        return "tier_3_blocked"
    if p in TIER_0_PARAMS:
        return "tier_0_auto"
    if p in TIER_1_PARAMS:
        return "tier_1_notify"
    return "tier_2_require_approval"


def meets_auto_approve_criteria(shadow_results: dict[str, Any]) -> bool:
    """Tier-0 gate: strict when win_rate_delta exists; proxy otherwise."""
    criteria = AUTO_APPROVE_CRITERIA
    n = int(shadow_results.get("decision_count") or shadow_results.get("sample_decisions") or 0)
    if n < int(criteria["min_sample_size"]):
        # Soft proxy: enough activity in shadow sample
        if n >= 5 and int(shadow_results.get("threshold_cross_flips") or 0) >= 2:
            pass
        else:
            return False

    wr = shadow_results.get("win_rate_delta")
    if wr is not None:
        if float(wr) < float(criteria["min_win_rate_improvement"]):
            return False
    else:
        erd = shadow_results.get("execution_rate_delta")
        if erd is not None and float(erd) < 0.0:
            return False
        if int(shadow_results.get("threshold_cross_flips") or 0) < 1 and (
            shadow_results.get("parameter_tested") == "confidence_threshold"
        ):
            return False

    dd = shadow_results.get("max_drawdown_delta")
    if dd is not None and float(dd) > float(criteria["max_drawdown_increase"]):
        return False

    return True


def sync_tunable_params_snapshot() -> None:
    from agents.self_improvement_engine import SelfImprovementEngine

    eng = SelfImprovementEngine()
    tunable_params_snapshot_path().parent.mkdir(parents=True, exist_ok=True)
    tunable_params_snapshot_path().write_text(
        json.dumps(eng.current_tunable_snapshot(), indent=2),
        encoding="utf-8",
    )


class ImprovementGovernance:
    def __init__(self) -> None:
        _data_dir().mkdir(parents=True, exist_ok=True)

    def log_proposal_record(self, record: dict[str, Any]) -> None:
        record = {**record, "logged_at": datetime.now(timezone.utc).isoformat()}
        with open(proposals_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def log_decision(self, decision: dict[str, Any]) -> None:
        decision = {**decision, "timestamp": datetime.now(timezone.utc).isoformat()}
        with open(governance_decisions_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(decision, default=str) + "\n")

    def log_outcome(self, outcome: dict[str, Any]) -> None:
        outcome = {**outcome, "logged_at": datetime.now(timezone.utc).isoformat()}
        with open(outcomes_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(outcome, default=str) + "\n")

    def apply_change(self, proposal: dict[str, Any], proposal_id: str) -> None:
        from agents.self_improvement_engine import SelfImprovementEngine

        SelfImprovementEngine()._apply_proposal(proposal, proposal_id)
        sync_tunable_params_snapshot()

    def process_expired_veto_windows(self) -> list[dict[str, Any]]:
        """Apply Tier-1 proposals whose veto deadline passed without veto record."""
        p = veto_pending_path()
        if not p.exists():
            return []
        try:
            pending = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []

        if not pending.get("proposal_id"):
            return []

        deadline_raw = pending.get("veto_deadline") or ""
        try:
            deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
        except Exception:
            return []

        now = datetime.now(timezone.utc)
        if now < deadline:
            return []

        veto_exists = self._veto_record_exists(str(pending.get("proposal_id")))
        out: list[dict[str, Any]] = []
        if not veto_exists:
            prop = pending.get("proposal") or {}
            pid = str(pending.get("proposal_id"))
            self.apply_change(prop, pid)
            rec = {
                "proposal_id": pid,
                "decision": "auto_approved_after_veto_window",
                "reason": "no_veto_within_24h",
            }
            self.log_decision(rec)
            self.log_outcome(
                {
                    "proposal_id": pid,
                    "parameter": prop.get("parameter"),
                    "old_value": prop.get("current_value"),
                    "new_value": prop.get("proposed_value"),
                    "status": "active",
                    "via": "tier_1_veto_expiry",
                }
            )
            out.append(rec)
        try:
            p.unlink()
        except OSError:
            pass
        return out

    def _veto_record_exists(self, proposal_id: str) -> bool:
        gd = governance_decisions_path()
        if not gd.exists():
            return False
        try:
            for line in gd.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("proposal_id") == proposal_id and o.get("decision") == "vetoed_by_human":
                    return True
        except Exception:
            pass
        return False

    def process_proposal(
        self,
        *,
        proposal: dict[str, Any],
        proposal_id: str,
        shadow_results: dict[str, Any],
        engine_rec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Route by tier. Mutates nothing if tier_3.
        Returns decision summary for logging / API.
        """
        param = str(proposal.get("parameter") or "")
        tier = determine_governance_tier(param)

        self.log_proposal_record(
            {
                "proposal_id": proposal_id,
                "proposal": proposal,
                "shadow_results": shadow_results,
                "governance_tier": tier,
                "engine_rec": engine_rec,
            }
        )

        if tier == "tier_3_blocked":
            d = {
                "proposal_id": proposal_id,
                "decision": "blocked_tier_3",
                "governance_tier": tier,
                "reason": "immutable_constraint_parameter",
            }
            self.log_decision(d)
            return d

        if tier == "tier_0_auto":
            return self._process_tier_0(proposal, proposal_id, shadow_results)

        if tier == "tier_1_notify":
            return self._process_tier_1(proposal, proposal_id, shadow_results)

        d = {
            "proposal_id": proposal_id,
            "decision": "requires_explicit_approval",
            "governance_tier": tier,
            "status": "Use dashboard pending + POST approve",
        }
        self.log_decision(d)
        return d

    def _process_tier_0(self, proposal: dict[str, Any], proposal_id: str, shadow: dict[str, Any]) -> dict[str, Any]:
        if meets_auto_approve_criteria(shadow):
            self.apply_change(proposal, proposal_id)
            self.log_outcome(
                {
                    "proposal_id": proposal_id,
                    "parameter": proposal.get("parameter"),
                    "old_value": proposal.get("current_value"),
                    "new_value": proposal.get("proposed_value"),
                    "status": "active",
                    "via": "tier_0_auto",
                }
            )
            d = {
                "proposal_id": proposal_id,
                "decision": "auto_approved",
                "governance_tier": "tier_0_auto",
                "shadow_results": shadow,
            }
            self.log_decision(d)
            return d

        d = {
            "proposal_id": proposal_id,
            "decision": "escalated_pending_human",
            "governance_tier": "tier_0_auto",
            "reason": "shadow_criteria_not_met",
            "shadow_results": shadow,
        }
        self.log_decision(d)
        return d

    def _process_tier_1(self, proposal: dict[str, Any], proposal_id: str, shadow: dict[str, Any]) -> dict[str, Any]:
        veto_deadline = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        veto_pending_path().write_text(
            json.dumps(
                {
                    "proposal_id": proposal_id,
                    "proposal": proposal,
                    "shadow_results": shadow,
                    "veto_deadline": veto_deadline,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        d = {
            "proposal_id": proposal_id,
            "decision": "pending_veto_window",
            "governance_tier": "tier_1_notify",
            "veto_deadline": veto_deadline,
            "shadow_results": shadow,
            "status": "Human may veto within 24h; call POST /api/governance/process-veto-windows or cron.",
        }
        self.log_decision(d)
        return d

    def veto_proposal(self, proposal_id: str) -> dict[str, Any]:
        rec = {
            "proposal_id": proposal_id,
            "decision": "vetoed_by_human",
        }
        self.log_decision(rec)
        if veto_pending_path().exists():
            try:
                pending = json.loads(veto_pending_path().read_text(encoding="utf-8"))
                if pending.get("proposal_id") == proposal_id:
                    veto_pending_path().unlink()
            except Exception:
                pass
        return rec
