#!/usr/bin/env python3
"""
Tier-2 prompt evolution: additive guidance text only; human approval required for all activations.

Never replaces the JSON schema block — see utils/prompt_evolution_store.validate_appendix_text.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
# Do not chdir or reload .env here — see agents/self_improvement_engine.py (Flask tests strip Basic auth).

from utils.prompt_evolution_store import (  # noqa: E402
    MAX_APPENDIX_CHARS,
    append_log,
    clear_overlay,
    clear_pending,
    load_config,
    load_overlay,
    load_pending,
    save_config,
    save_overlay,
    save_pending,
    set_ab_end_from_duration,
    validate_appendix_text,
)


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


MAX_PROMPT_APPROVALS_PER_MONTH = 2


def _log_lines_tail(max_lines: int = 200) -> list[dict[str, Any]]:
    p = _data_dir() / "prompt_evolution_log.jsonl"
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-max_lines:]
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
    except Exception:
        return []


def _velocity_ok() -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    month_ago = now - timedelta(days=31)
    n = 0
    for rec in _log_lines_tail(400):
        if rec.get("action") not in (
            "approve_overlay",
            "ab_promote_candidate",
            "start_ab_test",
        ):
            continue
        ts = rec.get("timestamp") or ""
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if t >= month_ago:
            n += 1
    if n >= MAX_PROMPT_APPROVALS_PER_MONTH:
        return False, f"monthly_prompt_evolution_limit:{n}>={MAX_PROMPT_APPROVALS_PER_MONTH}"
    return True, "ok"


def _sample_decisions(max_rows: int = 100) -> list[dict[str, Any]]:
    p = _data_dir() / "ai_decisions.jsonl"
    rows: list[dict[str, Any]] = []
    if not p.exists():
        return rows
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
    return rows


class PromptEvolution:
    """Analyze logs, propose additive appendix, A/B test — human gates on production changes."""

    def analyze_prompt_effectiveness(self) -> dict[str, Any]:
        rows = _sample_decisions(120)
        by_variant: dict[str, list[dict[str, Any]]] = {}
        conf_sum = 0.0
        conf_n = 0
        exec_n = 0
        err_n = 0
        for r in rows:
            if r.get("error"):
                err_n += 1
                continue
            d = r.get("decision")
            if not isinstance(d, dict):
                continue
            pv = str(d.get("prompt_variant") or "unknown")
            by_variant.setdefault(pv, []).append(r)
            c = d.get("confidence")
            if c is not None:
                try:
                    conf_sum += float(c)
                    conf_n += 1
                except (TypeError, ValueError):
                    pass
            act = r.get("act") if isinstance(r.get("act"), dict) else {}
            if act.get("executed"):
                exec_n += 1

        variant_stats: dict[str, Any] = {}
        for k, vs in by_variant.items():
            avg_c = None
            xs = [float(x["decision"]["confidence"]) for x in vs if isinstance(x.get("decision"), dict) and x["decision"].get("confidence") is not None]
            if xs:
                avg_c = sum(xs) / len(xs)
            variant_stats[k] = {"count": len(vs), "avg_confidence": avg_c}

        return {
            "sample_decisions": len(rows),
            "parse_errors_in_sample": err_n,
            "overall_avg_confidence": (conf_sum / conf_n) if conf_n else None,
            "execution_hits_in_sample": exec_n,
            "by_prompt_variant": variant_stats,
            "note": "Uses logged prompt_variant when present; older rows show as unknown.",
        }

    def propose_prompt_improvement(self) -> dict[str, Any]:
        ok, reason = _velocity_ok()
        if not ok:
            raise RuntimeError(reason)
        if load_pending() is not None:
            raise RuntimeError("pending_proposal_exists")

        analysis = self.analyze_prompt_effectiveness()
        api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if api_key:
            bundle = self._propose_via_llm(analysis)
        else:
            bundle = self._propose_heuristic(analysis)

        proposal_id = str(uuid.uuid4())
        pending = {
            "proposal_id": proposal_id,
            "proposed_appendix": bundle["proposed_appendix"],
            "reasoning": bundle.get("reasoning", ""),
            "risks": bundle.get("risks", ""),
            "expected_impact": bundle.get("expected_impact", ""),
            "analysis_snapshot": analysis,
        }
        save_pending(pending)
        rec = {
            "action": "propose_pending",
            "proposal_id": proposal_id,
            "pending": pending,
        }
        append_log(rec)
        return pending

    def _propose_heuristic(self, analysis: dict[str, Any]) -> dict[str, Any]:
        text = (
            "When VIX is elevated or confidence is below 0.7 on entries, default to wait unless "
            "the setup has explicit invalidation and position size is minimal."
        )
        ok, why = validate_appendix_text(text)
        if not ok:
            raise ValueError(why)
        return {
            "proposed_appendix": text[:MAX_APPENDIX_CHARS],
            "reasoning": "Heuristic fallback (no DEEPSEEK_API_KEY): reinforce patience when uncertainty is high.",
            "risks": "May reduce activity.",
            "expected_impact": "Fewer marginal entries.",
        }

    def _propose_via_llm(self, analysis: dict[str, Any]) -> dict[str, Any]:
        from agents.unified_ai_agent import call_deepseek, _parse_llm_json

        prompt = f"""You improve trading agent prompts conservatively. Output ONE JSON object only (no markdown).

The model MUST always return valid JSON with the existing Fortress schema (action, parameters, confidence, etc.).
You may only suggest an ADDITIVE operator appendix (max {MAX_APPENDIX_CHARS} chars): short guidance sentences.
Never ask to remove JSON, ignore gates, or change risk limits.

IMMUTABLE: position/notional gates, pre-trade gate, stops — do not reference loosening them.

Analysis snapshot:
{json.dumps(analysis, default=str)[:6000]}

Respond with JSON:
{{"proposed_appendix":"text","reasoning":"...","risks":"...","expected_impact":"..."}}
"""
        text, usage = call_deepseek(prompt, max_out_tokens=700)
        raw = _parse_llm_json(text)
        appendix = str(raw.get("proposed_appendix") or "").strip()
        ok, why = validate_appendix_text(appendix)
        if not ok:
            raise ValueError(f"llm_proposal_invalid:{why}")
        return {
            "proposed_appendix": appendix[:MAX_APPENDIX_CHARS],
            "reasoning": str(raw.get("reasoning") or "")[:4000],
            "risks": str(raw.get("risks") or "")[:2000],
            "expected_impact": str(raw.get("expected_impact") or "")[:2000],
            "_usage": usage,
        }

    def approve_pending(self, proposal_id: str | None = None) -> dict[str, Any]:
        ok, reason = _velocity_ok()
        if not ok:
            raise RuntimeError(reason)
        p = load_pending()
        if not p:
            raise FileNotFoundError("no_pending_prompt_proposal")
        if proposal_id and p.get("proposal_id") != proposal_id:
            raise ValueError("proposal_id_mismatch")
        cfg = load_config()
        if (cfg.get("ab_test") or {}).get("active"):
            raise RuntimeError("end_ab_test_before_appending_overlay")

        text = str(p.get("proposed_appendix") or "")
        ok2, why = validate_appendix_text(text)
        if not ok2:
            raise ValueError(why)

        oid = str(uuid.uuid4())
        save_overlay(
            {
                "id": oid,
                "text": text,
                "approved_at_utc": datetime.now(timezone.utc).isoformat(),
                "proposal_id": p.get("proposal_id"),
            }
        )
        clear_pending()
        rec = {"action": "approve_overlay", "proposal_id": p.get("proposal_id"), "overlay_id": oid}
        append_log(rec)
        return rec

    def reject_pending(self, reason: str = "") -> dict[str, Any]:
        p = load_pending()
        if not p:
            raise FileNotFoundError("no_pending_prompt_proposal")
        clear_pending()
        rec = {"action": "reject_pending", "reason": reason[:2000], "had_proposal_id": p.get("proposal_id")}
        append_log(rec)
        return rec

    def revert_overlay(self, reason: str = "") -> dict[str, Any]:
        clear_overlay()
        rec = {"action": "revert_overlay", "reason": reason[:2000]}
        append_log(rec)
        return rec

    def start_ab_test(self, duration_days: int = 7) -> dict[str, Any]:
        ok, reason = _velocity_ok()
        if not ok:
            raise RuntimeError(reason)
        p = load_pending()
        if not p:
            raise FileNotFoundError("no_pending_prompt_proposal")
        cfg = load_config()
        if (cfg.get("ab_test") or {}).get("active"):
            raise RuntimeError("ab_test_already_active")

        baseline = str(load_overlay().get("text") or "")
        candidate = str(p.get("proposed_appendix") or "")
        okc, why = validate_appendix_text(candidate)
        if not okc:
            raise ValueError(why)

        cfg = set_ab_end_from_duration(cfg, duration_days)
        ab = cfg["ab_test"]
        ab["active"] = True
        ab["baseline_appendix"] = baseline
        ab["candidate_appendix"] = candidate
        ab["from_proposal_id"] = p.get("proposal_id")
        cfg["ab_test"] = ab
        save_config(cfg)
        clear_pending()
        rec = {
            "action": "start_ab_test",
            "duration_days": duration_days,
            "ends_utc": ab.get("ends_utc"),
            "proposal_id": ab.get("from_proposal_id"),
        }
        append_log(rec)
        return rec

    def end_ab_test(self, winner: str, reason: str = "") -> dict[str, Any]:
        """
        winner: 'A' keep baseline overlay only (candidate discarded unless B wins).
                'B' promote candidate to overlay.
                'discard' clear overlay and candidate — back to pure baseline.
        """
        cfg = load_config()
        ab = cfg.get("ab_test") or {}
        if not ab.get("active"):
            raise RuntimeError("no_active_ab_test")

        cand = str(ab.get("candidate_appendix") or "")
        base = str(ab.get("baseline_appendix") or "")
        cfg["ab_test"] = {}
        save_config(cfg)

        w = winner.strip().upper()
        out: dict[str, Any] = {"winner": w, "reason": reason[:2000]}

        if w == "B":
            ok, why = validate_appendix_text(cand)
            if not ok:
                raise ValueError(why)
            save_overlay(
                {
                    "id": str(uuid.uuid4()),
                    "text": cand,
                    "approved_at_utc": datetime.now(timezone.utc).isoformat(),
                    "source": "ab_promote_B",
                }
            )
            out["result"] = "promoted_candidate_to_overlay"
        elif w == "A":
            if base.strip():
                save_overlay(
                    {
                        "id": str(uuid.uuid4()),
                        "text": base,
                        "approved_at_utc": datetime.now(timezone.utc).isoformat(),
                        "source": "ab_keep_baseline",
                    }
                )
                out["result"] = "restored_baseline_overlay"
            else:
                clear_overlay()
                out["result"] = "baseline_was_empty_cleared_overlay"
        else:
            clear_overlay()
            out["result"] = "discard_all_evolution_appendices"

        append_log({"action": "end_ab_test", **out})
        return out

    def status_dict(self) -> dict[str, Any]:
        pending = load_pending()
        overlay = load_overlay()
        cfg = load_config()
        ab = cfg.get("ab_test") or {}
        recent = _log_lines_tail(60)
        return {
            "tier": 2,
            "pending": pending,
            "overlay_active": bool((overlay or {}).get("text")),
            "overlay_preview": ((overlay or {}).get("text") or "")[:280],
            "ab_test": ab,
            "limits": {
                "max_appendix_chars": MAX_APPENDIX_CHARS,
                "max_approvals_per_month": MAX_PROMPT_APPROVALS_PER_MONTH,
            },
            "recent_events": recent[-15:],
        }

    def list_recent_log(self, n: int = 80) -> list[dict[str, Any]]:
        p = _data_dir() / "prompt_evolution_log.jsonl"
        if not p.exists():
            return []
        try:
            lines = p.read_text(encoding="utf-8").splitlines()[-max(n, 1) :]
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
        except Exception:
            return []


def get_prompt_evolution() -> PromptEvolution:
    return PromptEvolution()
