"""
Heuristic regime/sector + compact domain prompt injection + dashboard snapshot.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from knowledge.domain_knowledge import DomainKnowledge, _repo_root

_TICKER_SECTOR: dict[str, str] = {
    "AAPL": "technology",
    "MSFT": "technology",
    "GOOGL": "technology",
    "GOOG": "technology",
    "META": "technology",
    "NVDA": "technology",
    "AMD": "technology",
    "AMZN": "technology",
    "XLK": "technology",
    "XLV": "utilities",
    "XLU": "utilities",
    "JPM": "financials",
    "BAC": "financials",
    "GS": "financials",
    "SPY": "technology",
    "QQQ": "technology",
    "IWM": "technology",
}


def _domain_enabled() -> bool:
    return str(os.environ.get("FORTRESS_AI_DOMAIN_INTEL", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def infer_regime(observation: dict[str, Any]) -> str:
    try:
        vix = float(observation.get("vix_last") or 0)
    except (TypeError, ValueError):
        vix = 0.0
    if vix >= 24:
        return "BEAR_TREND"
    if vix <= 12 and vix > 0:
        return "BULL_TREND"
    return "NEUTRAL_RANGING"


def infer_sector(ticker: str | None) -> str:
    if not ticker:
        return ""
    return _TICKER_SECTOR.get(str(ticker).strip().upper(), "")


def infer_strategy(observation: dict[str, Any], state: dict[str, Any]) -> str:
    b = state.get("beliefs") if isinstance(state.get("beliefs"), dict) else {}
    s = str(b.get("preferred_strategy") or "mean_reversion").strip().lower()
    return s if s else "mean_reversion"


def build_domain_prompt_appendix(observation: dict[str, Any], state: dict[str, Any]) -> str:
    if not _domain_enabled():
        return ""
    dk = DomainKnowledge()
    regime = infer_regime(observation)
    strategy = infer_strategy(observation, state)
    sector = ""
    for p in observation.get("positions") or []:
        if isinstance(p, dict) and float(p.get("qty") or 0) != 0:
            sector = infer_sector(str(p.get("sym") or ""))
            if sector:
                break
    rel = dk.get_relevant_knowledge({"regime": regime, "sector": sector, "strategy": strategy})
    blob = {
        "regime_label": regime,
        "strategy": strategy,
        "sector": sector or "unknown",
        "domain": rel,
    }
    s = json.dumps(blob, separators=(",", ":"), default=str)
    max_chars = int(os.environ.get("FORTRESS_AI_DOMAIN_INTEL_MAX_CHARS", "1400"))
    if len(s) > max_chars:
        s = s[:max_chars] + "…"
    return s


def domain_intel_snapshot(
    macro: dict[str, Any] | None = None,
    *,
    beliefs: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """UI + API snapshot: regime from VIX macro, counts, recent learnings tail."""
    macro = macro or {}
    root = root or _repo_root()
    vix = macro.get("vix")
    if vix is None:
        vix = macro.get("vix_last")
    spy = macro.get("spy")
    if spy is None:
        spy = macro.get("spy_last")
    obs = {"vix_last": vix, "spy_last": spy}
    state = {"beliefs": beliefs or {}}
    out: dict[str, Any] = {
        "enabled": _domain_enabled(),
        "regime_hint": infer_regime(obs),
        "strategy_hint": infer_strategy(obs, state),
        "rsi_hint": macro.get("rsi"),
        "concept_counts": {},
        "total_concepts": 0,
        "recent_learnings": [],
        "llm_learn_enabled": str(os.environ.get("FORTRESS_AI_DOMAIN_LLM_LEARN", "0")).strip().lower()
        in ("1", "true", "yes", "on"),
    }
    if not out["enabled"]:
        return out
    try:
        dk = DomainKnowledge(root)
        counts = dk.concept_counts()
        out["concept_counts"] = counts
        out["total_concepts"] = int(sum(counts.values()))
        learn_path = dk.knowledge_dir / "learnings.jsonl"
        if learn_path.exists():
            for line in learn_path.read_text(encoding="utf-8").splitlines()[-12:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    out["recent_learnings"].append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:
        out["error"] = str(e)[:200]
    return out
