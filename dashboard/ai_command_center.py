#!/usr/bin/env python3
"""Fortress AI Command Center — port 8084 by default. Kill switch API compatible with Classic."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env", override=False)
except Exception:
    pass

from flask import Flask, jsonify, render_template, request

from utils.operator_halt import get_halt_state, set_trading_halt

app = Flask(__name__, template_folder=Path(__file__).resolve().parent / "templates")


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


@app.route("/")
def index():
    return render_template("ai_dashboard.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "fortress-ai-dashboard"})


@app.route("/api/fortress_ai/status")
def ai_status():
    out: dict = {"instance": os.environ.get("FORTRESS_INSTANCE_NAME", "Fortress-AI")}
    latest = _data_dir() / "ai_latest_metric.json"
    if latest.exists():
        try:
            out["latest_metric"] = json.loads(latest.read_text(encoding="utf-8"))
        except Exception:
            out["latest_metric"] = None
    state_path = _data_dir() / "ai_state.json"
    if state_path.exists():
        try:
            out["state_preview"] = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    out["dry_run"] = str(os.environ.get("FORTRESS_AI_DRY_RUN", "1")).lower() in ("1", "true", "yes")
    try:
        out["min_confidence"] = float(os.environ.get("FORTRESS_AI_MIN_CONFIDENCE", "0.8"))
    except ValueError:
        out["min_confidence"] = 0.8
    try:
        out["weekly_cost_cap_usd"] = float(os.environ.get("FORTRESS_AI_WEEKLY_COST_CAP_USD", "1.0"))
    except ValueError:
        out["weekly_cost_cap_usd"] = 1.0
    return jsonify(out)


@app.route("/api/operator/halt", methods=["GET"])
def api_halt_get():
    return jsonify(get_halt_state())


@app.route("/api/operator/halt", methods=["POST"])
def api_halt_post():
    """Same shape as Classic Command Center for shared operator tooling."""
    active = bool(request.json.get("active")) if request.is_json else request.form.get("active") == "1"
    reason = (request.json.get("reason") if request.is_json else request.form.get("reason")) or ""
    actor = (request.json.get("actor") if request.is_json else request.form.get("actor")) or "dashboard"
    st = set_trading_halt(active, reason=str(reason), actor=str(actor))
    return jsonify({"ok": True, "state": get_halt_state(), "file": st})


if __name__ == "__main__":
    port = int(os.environ.get("FORTRESS_AI_DASHBOARD_PORT") or os.environ.get("DASHBOARD_PORT") or "8084")
    app.run(host="0.0.0.0", port=port, debug=False)
