"""
Extract and persist lessons. Optional LLM extraction when env is enabled.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge.domain_knowledge import DomainKnowledge, _repo_root


class LearningEngine:
    """Append-only learnings + optional merge into DomainKnowledge."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _repo_root()
        self.knowledge_dir = self.root / "data" / "domain_knowledge"
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.learnings_path = self.knowledge_dir / "learnings.jsonl"
        self.domain_knowledge = DomainKnowledge(self.root)

    def record_lesson(self, lesson: dict[str, Any]) -> None:
        row = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            **lesson,
        }
        with open(self.learnings_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def learn_from_trade_outcome(self, trade: dict[str, Any], outcome: dict[str, Any]) -> None:
        """Optional LLM extraction; otherwise store a compact heuristic row."""
        if str(os.environ.get("FORTRESS_AI_DOMAIN_LLM_LEARN", "0")).strip().lower() not in (
            "1",
            "true",
            "yes",
            "on",
        ):
            self.record_lesson(
                {
                    "category": "heuristic",
                    "insight": f"Recorded outcome for {trade.get('symbol') or trade.get('ticker')!s}: {outcome}",
                    "confidence": 0.35,
                    "trade": trade,
                    "outcome": outcome,
                }
            )
            return
        prompt = (
            "Extract 1-3 lessons as JSON only: {\"lessons\":[{\"category\":\"regime_behavior|sector_pattern|"
            "strategy_effectiveness|risk_event\",\"insight\":\"...\",\"confidence\":0.0,\"applicable_to\":[]}]}\n\n"
            f"TRADE:{json.dumps(trade, default=str)[:4000]}\nOUTCOME:{json.dumps(outcome, default=str)[:4000]}"
        )
        try:
            from agents.unified_ai_agent import call_deepseek, _parse_llm_json

            text, _ = call_deepseek(prompt, max_out_tokens=900)
            parsed = _parse_llm_json(text)
        except Exception as e:
            self.record_lesson(
                {
                    "category": "llm_error",
                    "insight": str(e)[:240],
                    "confidence": 0.0,
                    "trade": trade,
                    "outcome": outcome,
                }
            )
            return
        for lesson in parsed.get("lessons") or []:
            if not isinstance(lesson, dict):
                continue
            self.store_lesson(lesson)

    def store_lesson(self, lesson: dict[str, Any]) -> None:
        self.record_lesson(lesson)
        category = str(lesson.get("category") or "behavioral_patterns")
        domain_map = {
            "regime_behavior": "market_regimes",
            "sector_pattern": "sector_dynamics",
            "strategy_effectiveness": "trading_strategies",
            "risk_event": "risk_frameworks",
        }
        domain = domain_map.get(category, "behavioral_patterns")
        concept = f"learned_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self.domain_knowledge.add_learned_knowledge(domain=domain, concept=concept, knowledge=lesson)
