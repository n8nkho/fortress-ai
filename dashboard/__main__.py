"""Run the dashboard with: python3 -m dashboard (from fortress-ai repo root)."""

from __future__ import annotations

import os

from dashboard.ai_command_center import app


def main() -> None:
    port = int(os.environ.get("FORTRESS_AI_DASHBOARD_PORT") or os.environ.get("DASHBOARD_PORT") or "8050")
    host = (os.environ.get("FORTRESS_AI_DASHBOARD_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
