"""
Tier-2 prompt evolution: additive appendix only (never replaces core JSON schema instructions).

Files under FORTRESS_AI_DATA_DIR or ./data:
  prompt_evolution_overlay.json   — human-approved active appendix
  prompt_evolution_pending.json   — proposal awaiting approval / A/B start
  prompt_evolution_config.json    — A/B test window + candidate/baseline text
  prompt_evolution_log.jsonl      — audit trail
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent / "data"


def overlay_path() -> Path:
    return _data_dir() / "prompt_evolution_overlay.json"


def pending_path() -> Path:
    return _data_dir() / "prompt_evolution_pending.json"


def config_path() -> Path:
    return _data_dir() / "prompt_evolution_config.json"


def log_path() -> Path:
    return _data_dir() / "prompt_evolution_log.jsonl"


def load_overlay() -> dict[str, Any]:
    p = overlay_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_overlay(d: dict[str, Any]) -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)
    overlay_path().write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_overlay() -> None:
    p = overlay_path()
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def load_pending() -> dict[str, Any] | None:
    p = pending_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_pending(d: dict[str, Any]) -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)
    pending_path().write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_pending() -> None:
    p = pending_path()
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def load_config() -> dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {"ab_test": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"ab_test": {}}


def save_config(d: dict[str, Any]) -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


MAX_APPENDIX_CHARS = 1200

# Phrases that must not appear in operator appendix (case-insensitive).
_BLOCKLIST = (
    "ignore previous",
    "ignore the constraints",
    "bypass the gate",
    "bypass gate",
    "no json",
    "without json",
    "omit json",
    "system prompt",
    "you are now",
    "disregard",
    "max_total_exposure",
    "max_position_size",
    "increase exposure",
    "loosen risk",
)


def validate_appendix_text(text: str) -> tuple[bool, str]:
    s = (text or "").strip()
    if not s:
        return False, "empty"
    if len(s) > MAX_APPENDIX_CHARS:
        return False, f"too_long_max_{MAX_APPENDIX_CHARS}"
    low = s.lower()
    for phrase in _BLOCKLIST:
        if phrase in low:
            return False, f"blocked_phrase:{phrase}"
    if re.search(r"```\s*json", low):
        return False, "blocked_markdown_json"
    return True, "ok"


def append_log(record: dict[str, Any]) -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)
    record = {**record, "timestamp": datetime.now(timezone.utc).isoformat()}
    with open(log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def get_prompt_appendix_for_cycle(state: dict[str, Any]) -> tuple[str, str]:
    """
    Returns (appendix_text, variant_label) for logging and A/B attribution.
    When an A/B test is active, alternates baseline vs candidate appendix.
    """
    cfg = load_config()
    ab = cfg.get("ab_test") or {}
    now = datetime.now(timezone.utc)
    if ab.get("active"):
        ends = _parse_iso(ab.get("ends_utc"))
        if ends is not None and now > ends:
            # Expired — treat as inactive (operator should end_ab or we fall through)
            pass
        else:
            base = str(ab.get("baseline_appendix") or "").strip()
            cand = str(ab.get("candidate_appendix") or "").strip()
            flip = len(state.get("last_actions") or []) % 2
            if flip == 0:
                return base, "A_baseline"
            return cand, "B_candidate"

    ov = load_overlay()
    text = str(ov.get("text") or "").strip()
    if text:
        return text, "overlay"
    return "", "baseline"


def ab_test_expired_raw(cfg: dict[str, Any]) -> bool:
    ab = cfg.get("ab_test") or {}
    if not ab.get("active"):
        return False
    ends = _parse_iso(ab.get("ends_utc"))
    if ends is None:
        return False
    return datetime.now(timezone.utc) > ends


def set_ab_end_from_duration(cfg: dict[str, Any], duration_days: int) -> dict[str, Any]:
    ab = dict(cfg.get("ab_test") or {})
    start = datetime.now(timezone.utc)
    ab["started_utc"] = start.isoformat()
    ab["ends_utc"] = (start + timedelta(days=max(1, int(duration_days)))).isoformat()
    cfg = dict(cfg)
    cfg["ab_test"] = ab
    return cfg
