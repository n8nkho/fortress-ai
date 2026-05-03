"""Flask routes — risk-based governance API (tiers 0–3)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import jsonify, request


def register_governance_routes(app, *, data_dir_fn=None) -> None:
    """Attach governance endpoints to the dashboard app."""

    def _data_dir() -> Path:
        if data_dir_fn:
            return Path(data_dir_fn())
        import os

        raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
        root = Path(__file__).resolve().parent.parent
        return Path(raw) if raw else (root / "data")

    @app.route("/api/governance/pending")
    def governance_pending():
        from utils.improvement_governance import veto_pending_path

        out: dict[str, Any] = {"veto_pending": None, "notes": []}
        vp = veto_pending_path()
        if vp.exists():
            try:
                out["veto_pending"] = json.loads(vp.read_text(encoding="utf-8"))
            except Exception:
                out["veto_pending"] = {"error": "read_failed"}
        return jsonify(out)

    @app.route("/api/governance/approve/<proposal_id>", methods=["POST"])
    def governance_approve(proposal_id: str):
        from agents.self_improvement_engine import get_engine

        try:
            rec = get_engine().approve_pending(proposal_id=proposal_id)
            return jsonify({"ok": True, "record": rec})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/governance/veto/<proposal_id>", methods=["POST"])
    def governance_veto(proposal_id: str):
        from utils.improvement_governance import ImprovementGovernance

        try:
            rec = ImprovementGovernance().veto_proposal(proposal_id)
            return jsonify({"ok": True, "record": rec})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/governance/history")
    def governance_history():
        from utils.improvement_governance import governance_decisions_path

        gd = governance_decisions_path()
        rows: list[dict[str, Any]] = []
        if gd.exists():
            try:
                for line in gd.read_text(encoding="utf-8").splitlines()[-500:]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass
        return jsonify({"history": rows})

    @app.route("/api/governance/process-veto-windows", methods=["POST"])
    def governance_process_veto_windows():
        from utils.improvement_governance import ImprovementGovernance

        try:
            applied = ImprovementGovernance().process_expired_veto_windows()
            return jsonify({"ok": True, "applied": applied})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/governance/tiers")
    def governance_tiers():
        from utils.improvement_governance import (
            IMMUTABLE_PARAM_NAMES,
            TIER_0_PARAMS,
            TIER_1_PARAMS,
            AUTO_APPROVE_CRITERIA,
        )

        return jsonify(
            {
                "tier_0_params": sorted(TIER_0_PARAMS),
                "tier_1_params": sorted(TIER_1_PARAMS),
                "tier_3_immutable_names": sorted(IMMUTABLE_PARAM_NAMES),
                "tier_2_note": "prompt/strategy changes use /api/prompt_evolution/*",
                "auto_approve_criteria": AUTO_APPROVE_CRITERIA,
            }
        )

    @app.route("/api/governance/monitor", methods=["POST"])
    def governance_monitor():
        from agents.performance_monitor import PerformanceMonitor

        try:
            out = PerformanceMonitor().monitor_active_changes()
            return jsonify({"ok": True, "reversions": out})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
