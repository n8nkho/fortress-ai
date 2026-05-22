#!/usr/bin/env python3
"""
SPY intraday self-improvement — bounded recursive tuning for skim profitability.

Tunable: confidence, loop cadence, ladder rungs. Immutable: max exposure cap, EOD flat, pre_trade_gate.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from utils.spy_agent_config import max_exposure_usd, spy_data_dir
from utils.spy_tunable_overrides import (
    clear_overrides,
    current_snapshot,
    get_spy_min_confidence,
    load_overrides,
    save_overrides,
)

SPY_IMMUTABLE = {
    "max_exposure_usd_cap": "env FORTRESS_SPY_MAX_EXPOSURE_USD only — SI cannot raise above env",
    "eod_flat_required": True,
    "pre_trade_gate_required": True,
    "dry_run_toggle": "env only",
}

SPY_TUNABLE_BOUNDS: dict[str, dict[str, float]] = {
    "spy_min_confidence": {"min": 0.65, "max": 0.92},
    "spy_loop_seconds_rth": {"min": 120, "max": 600},
    "spy_loop_seconds_active": {"min": 60, "max": 360},
    "spy_ladder_rungs": {"min": 2, "max": 4},
}

# Map to governance tiers (reuse improvement_governance.py)
_GOV_PARAM_ALIAS = {
    "spy_min_confidence": "confidence_threshold",
    "spy_loop_seconds_rth": "decision_interval",
    "spy_loop_seconds_active": "rsi_entry_threshold",
    "spy_ladder_rungs": "position_size_pct",
}

MAX_CHANGES_PER_WEEK = 2
MAX_CHANGES_PER_MONTH = 6
SAFE_SKIM_RATE_FLOOR = 0.15


@dataclass
class SpyPerformanceSnapshot:
    cycles: int = 0
    executed: int = 0
    trims: int = 0
    adds: int = 0
    waits: int = 0
    avg_confidence: float | None = None
    skim_rate: float | None = None
    high_conf_wait_rate: float | None = None


def _log_path() -> Path:
    spy_data_dir().mkdir(parents=True, exist_ok=True)
    return spy_data_dir() / "spy_self_improvement_log.jsonl"


def _pending_path() -> Path:
    return spy_data_dir() / "spy_self_improvement_pending.json"


def _state_path() -> Path:
    return spy_data_dir() / "spy_self_improvement_state.json"


def _cycle_counter_path() -> Path:
    return spy_data_dir() / "spy_si_cycle_counter.json"


class SpySelfImprovementEngine:
    def __init__(self) -> None:
        spy_data_dir().mkdir(parents=True, exist_ok=True)
        if not _state_path().exists():
            _state_path().write_text(
                json.dumps({"halted": False, "halt_reason": None}, indent=2),
                encoding="utf-8",
            )

    def load_performance(self, max_rows: int = 120) -> SpyPerformanceSnapshot:
        p = spy_data_dir() / "decisions.jsonl"
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

        snap = SpyPerformanceSnapshot(cycles=len(rows))
        confs: list[float] = []
        high_conf_waits = 0
        th = get_spy_min_confidence()
        for r in rows:
            d = r.get("decision") if isinstance(r.get("decision"), dict) else {}
            act = r.get("act") if isinstance(r.get("act"), dict) else {}
            action = (d.get("action") or "").lower()
            if action == "wait":
                snap.waits += 1
                try:
                    if float(d.get("confidence") or 0) >= th:
                        high_conf_waits += 1
                except (TypeError, ValueError):
                    pass
            elif action in ("add_long", "add_short"):
                snap.adds += 1
            elif action == "trim":
                snap.trims += 1
            if act.get("executed"):
                snap.executed += 1
            c = d.get("confidence")
            if c is not None:
                try:
                    confs.append(float(c))
                except (TypeError, ValueError):
                    pass

        if confs:
            snap.avg_confidence = sum(confs) / len(confs)
        if rows:
            snap.skim_rate = snap.executed / len(rows)
            snap.high_conf_wait_rate = high_conf_waits / max(snap.waits, 1)
        return snap

    def validate_proposal(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        param = str(raw.get("parameter") or "").strip()
        if param not in SPY_TUNABLE_BOUNDS:
            return None
        try:
            pv = float(raw.get("proposed_value"))
        except (TypeError, ValueError):
            return None
        if param == "spy_ladder_rungs":
            pv = float(int(round(pv)))
        b = SPY_TUNABLE_BOUNDS[param]
        if pv < b["min"] or pv > b["max"]:
            return None
        cur = current_snapshot().get(param)
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
        from agents.unified_ai_agent import _parse_llm_json, call_deepseek

        perf = self.load_performance()
        tunable = current_snapshot()
        prompt = f"""You tune a SPY intraday skim bot (same-day ladder, max exposure ${max_exposure_usd():.0f}, EOD flat).

IMMUTABLE: {json.dumps(SPY_IMMUTABLE)}
BOUNDS: {json.dumps(SPY_TUNABLE_BOUNDS)}
CURRENT: {json.dumps(tunable)}

Stats (recent decisions):
- cycles: {perf.cycles}
- executed_rate (skim proxy): {perf.skim_rate}
- adds: {perf.adds} trims: {perf.trims} waits: {perf.waits}
- high_confidence_wait_rate: {perf.high_conf_wait_rate}
- avg_confidence: {perf.avg_confidence}

Goal: increase probability of small positive intraday skims without violating immutable risk.
Propose ONE parameter change within bounds.

JSON only:
{{"parameter":"spy_min_confidence|spy_loop_seconds_rth|spy_loop_seconds_active|spy_ladder_rungs","current_value":n,"proposed_value":n,"reasoning":"...","expected_impact":"...","risks":"..."}}
"""
        text, usage = call_deepseek(prompt, max_out_tokens=700)
        val = self.validate_proposal(_parse_llm_json(text))
        if not val:
            raise ValueError("invalid_proposal")
        return {"proposal": val, "usage": usage}

    def propose_heuristic(self) -> dict[str, Any]:
        perf = self.load_performance()
        tunable = current_snapshot()
        param = "spy_min_confidence"
        ct = float(tunable["spy_min_confidence"])
        new_ct = ct
        if (
            perf.high_conf_wait_rate
            and perf.high_conf_wait_rate > 0.4
            and perf.skim_rate is not None
            and perf.skim_rate < 0.1
        ):
            new_ct = max(SPY_TUNABLE_BOUNDS[param]["min"], ct - 0.02)
        elif perf.skim_rate is not None and perf.skim_rate > 0.35:
            new_ct = min(SPY_TUNABLE_BOUNDS[param]["max"], ct + 0.01)
        raw = {
            "parameter": param,
            "current_value": ct,
            "proposed_value": round(new_ct, 4),
            "reasoning": "Heuristic: balance wait rate vs skim executions.",
            "expected_impact": "Adjust trade frequency.",
            "risks": "More trades may increase costs/slippage.",
        }
        val = self.validate_proposal(raw)
        if not val:
            raise ValueError("heuristic_invalid")
        return {"proposal": val, "usage": {}}

    def _velocity_ok(self) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=31)
        week_n = month_n = 0
        lp = _log_path()
        if lp.exists():
            for line in lp.read_text(encoding="utf-8").splitlines():
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
        if week_n >= MAX_CHANGES_PER_WEEK:
            return False, f"weekly_limit:{week_n}"
        if month_n >= MAX_CHANGES_PER_MONTH:
            return False, f"monthly_limit:{month_n}"
        return True, "ok"

    def shadow_test(self, proposal: dict[str, Any]) -> dict[str, Any]:
        param = proposal.get("parameter")
        new_val = float(proposal.get("proposed_value") or 0)
        rows: list[dict] = []
        p = spy_data_dir() / "decisions.jsonl"
        if p.exists():
            for line in p.read_bytes()[-200_000:].decode("utf-8", errors="replace").split("\n")[-80:]:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        flips = 0
        if param == "spy_min_confidence" and rows:
            for r in rows:
                d = r.get("decision") if isinstance(r.get("decision"), dict) else {}
                try:
                    c = float(d.get("confidence") or 0)
                except (TypeError, ValueError):
                    continue
                old_e = c >= get_spy_min_confidence()
                new_e = c >= new_val
                if old_e != new_e:
                    flips += 1
        skim_delta = None
        if param == "spy_min_confidence" and rows and flips:
            old_exec = sum(
                1
                for r in rows
                if isinstance(r.get("decision"), dict)
                and float(r.get("decision", {}).get("confidence") or 0) >= get_spy_min_confidence()
                and (r.get("act") or {}).get("executed")
            )
            new_exec = sum(
                1
                for r in rows
                if isinstance(r.get("decision"), dict)
                and float(r.get("decision", {}).get("confidence") or 0) >= new_val
                and (r.get("act") or {}).get("executed")
            )
            skim_delta = (new_exec - old_exec) / max(len(rows), 1)
        return {
            "mode": "spy_proxy",
            "sample_decisions": len(rows),
            "threshold_cross_flips": flips,
            "execution_rate_delta": skim_delta,
            "parameter_tested": param,
            "win_rate_delta": skim_delta,
        }

    def _apply(self, proposal: dict[str, Any], change_id: str) -> None:
        cur = load_overrides()
        cur[proposal["parameter"]] = proposal["proposed_value"]
        cur["_meta"] = {"last_change_id": change_id, "applied_at_utc": datetime.now(timezone.utc).isoformat()}
        save_overrides(cur)

    def analyze_and_propose(self) -> dict[str, Any]:
        from utils.improvement_governance import determine_governance_tier, meets_auto_approve_criteria

        st = json.loads(_state_path().read_text(encoding="utf-8"))
        if st.get("halted"):
            raise RuntimeError(f"spy_si_halted:{st.get('halt_reason')}")

        ok, reason = self._velocity_ok()
        if not ok:
            raise RuntimeError(reason)

        min_cycles = int(os.environ.get("FORTRESS_SPY_SI_MIN_CYCLES", "8") or 8)
        perf = self.load_performance()
        if perf.cycles < min_cycles:
            return {"skipped": True, "reason": f"insufficient_cycles:{perf.cycles}<{min_cycles}"}

        api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        bundle = self.propose_via_llm() if api_key else self.propose_heuristic()
        proposal = bundle["proposal"]
        pid = str(uuid.uuid4())
        shadow = self.shadow_test(proposal)

        gov_param = _GOV_PARAM_ALIAS.get(str(proposal.get("parameter")), str(proposal.get("parameter")))
        tier = determine_governance_tier(gov_param)
        if tier == "tier_3_blocked":
            raise RuntimeError("tier_3_blocked")

        auto = str(os.environ.get("FORTRESS_SPY_SI_AUTO_APPLY", "1")).strip().lower() in ("1", "true", "yes")
        gdec = "pending_human"
        if tier == "tier_0_auto" and auto and meets_auto_approve_criteria(shadow):
            gdec = "auto_approved"
        elif tier == "tier_1_notify" and auto:
            gdec = "pending_veto_window"

        rec = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "proposal_id": pid,
            "proposal": proposal,
            "shadow_test": shadow,
            "governance_tier": tier,
            "decision": gdec,
            "performance": {
                "cycles": perf.cycles,
                "skim_rate": perf.skim_rate,
                "executed": perf.executed,
            },
        }

        if gdec == "auto_approved":
            self._apply(proposal, pid)
            rec["outcome"] = "applied"
        elif gdec == "pending_veto_window":
            rec["outcome"] = "pending_veto"
            veto_deadline = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            rec["veto_deadline"] = veto_deadline
            _pending_path().write_text(
                json.dumps({"proposal_id": pid, "proposal": proposal, "veto_deadline": veto_deadline}, indent=2),
                encoding="utf-8",
            )
        else:
            rec["outcome"] = "pending"
            _pending_path().write_text(json.dumps({"id": pid, **rec}, indent=2, default=str), encoding="utf-8")

        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def process_veto_windows(self) -> dict[str, Any] | None:
        """Apply pending tier-1 proposal after 24h veto window if not vetoed."""
        pp = _pending_path()
        if not pp.exists():
            return None
        try:
            pending = json.loads(pp.read_text(encoding="utf-8"))
        except Exception:
            return None
        deadline_raw = pending.get("veto_deadline") or ""
        if not deadline_raw:
            return None
        try:
            deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
        except Exception:
            return None
        if datetime.now(timezone.utc) < deadline:
            return None
        prop = pending.get("proposal") or {}
        pid = str(pending.get("proposal_id") or uuid.uuid4())
        self._apply(prop, pid)
        try:
            pp.unlink()
        except OSError:
            pass
        rec = {"decision": "auto_approved_after_veto_window", "proposal_id": pid}
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def maybe_improve_after_cycle(self) -> dict[str, Any] | None:
        """Called from spy agent loop — recursive SI every N cycles."""
        if str(os.environ.get("FORTRESS_SPY_SI_ENABLED", "1")).strip().lower() in ("0", "false", "no", "off"):
            return None
        self.process_veto_windows()
        every = max(3, int(os.environ.get("FORTRESS_SPY_SI_EVERY_N_CYCLES", "5") or 5))
        cp = _cycle_counter_path()
        n = 0
        if cp.exists():
            try:
                n = int(json.loads(cp.read_text(encoding="utf-8")).get("count", 0))
            except Exception:
                n = 0
        n += 1
        cp.write_text(json.dumps({"count": n, "updated_utc": datetime.now(timezone.utc).isoformat()}, indent=2))
        if n % every != 0:
            return None
        try:
            return self.analyze_and_propose()
        except Exception as e:
            return {"error": str(e)[:200], "cycle": n}

    def monitor_performance(self) -> dict[str, Any] | None:
        perf = self.load_performance()
        if perf.skim_rate is not None and perf.skim_rate < SAFE_SKIM_RATE_FLOOR and perf.cycles >= 20:
            clear_overrides()
            st = {"halted": True, "halt_reason": f"skim_rate_below_{SAFE_SKIM_RATE_FLOOR}"}
            _state_path().write_text(json.dumps(st, indent=2), encoding="utf-8")
            return {"reverted": True, "halted": True, "skim_rate": perf.skim_rate}
        return None

    def status_dict(self) -> dict[str, Any]:
        pending = None
        if _pending_path().exists():
            try:
                pending = json.loads(_pending_path().read_text(encoding="utf-8"))
            except Exception:
                pending = {"error": "read"}
        st = json.loads(_state_path().read_text(encoding="utf-8"))
        perf = self.load_performance()
        return {
            "immutable": SPY_IMMUTABLE,
            "bounds": SPY_TUNABLE_BOUNDS,
            "current": current_snapshot(),
            "overrides": load_overrides(),
            "pending": pending,
            "state": st,
            "performance": {
                "cycles": perf.cycles,
                "skim_rate": perf.skim_rate,
                "executed": perf.executed,
                "avg_confidence": perf.avg_confidence,
            },
            "velocity": {"max_per_week": MAX_CHANGES_PER_WEEK, "max_per_month": MAX_CHANGES_PER_MONTH},
        }


def get_spy_si_engine() -> SpySelfImprovementEngine:
    return SpySelfImprovementEngine()
