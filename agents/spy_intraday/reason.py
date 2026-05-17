"""LLM reasoning for index intraday ladder decisions."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from agents.spy_intraday.eod import filter_allowed_actions, is_eod_caution_window
from agents.unified_ai_agent import call_deepseek
from utils.spy_agent_config import index_symbol, max_exposure_usd

ALLOWED_ACTIONS = frozenset(
    {
        "wait",
        "add_long",
        "add_short",
        "trim",
        "flatten_all",
    }
)


def _parse_llm_json(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.IGNORECASE)
        if m:
            s = m.group(1).strip()
    return json.loads(s)


def build_prompt(observation: dict[str, Any]) -> str:
    sym = index_symbol()
    mkt = json.dumps(observation.get("market") or {}, separators=(",", ":"), default=str)[:7200]
    ladder = json.dumps(observation.get("ladder") or {}, separators=(",", ":"))[:800]
    pos = json.dumps(observation.get("position") or {}, separators=(",", ":"))[:400]
    eod = observation.get("eod_phase") or "normal"
    max_exp = max_exposure_usd()

    return f"""You are Fortress AI Index Intraday — specialized in {sym} (or DIA for Dow) **same-day** trades only.

MANDATES:
- Goal: small positive skims via scaled entry/exit ladder; multiple round-trips OK.
- Max total exposure ${max_exp:.0f} (sum of open ladder rungs). Flat by end of day — no overnight holds.
- Use long_term (multi-year trend), intraday swell (up/down/mixed), key_movers, regime_research, qualitative headlines.
- **futures**: ES/NQ/YM/RTY overnight % and tone — leading indicator for SPY (context only, do not trade futures).
- **global_sessions**: Asia (Nikkei, HSI, Kospi, Shanghai) and Europe (DAX, FTSE, Stoxx) day moves; session_clock_et phase.
  Before US open, weight Asia+Europe + futures heavily for gap risk; during RTH, use them for drift/conflict checks.
- Geopolitical/macro headlines in qualitative — weight non-quant risks explicitly (wars, rates, FX, China/EU policy).
- EOD phase: {eod}. If eod_caution or force_flatten: only trim or flatten_all.
- Shorting allowed on {sym} when bearish intraday structure; use add_short rungs (sell to open).

LADDER: scale in with add_long or add_short (one rung per action). trim removes one rung. flatten_all closes entire position.

MARKET_CONTEXT:{mkt}
LADDER_STATE:{ladder}
POSITION:{pos}

Respond ONE JSON object only:
{{"reasoning":"...","market_assessment":"one line","bias":"bullish|bearish|neutral","action":"<name>","confidence":0.0,"expected_outcome":"one line"}}

Actions: wait | add_long | add_short | trim | flatten_all"""


def reason(observation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = build_prompt(observation)
    max_chars = int(os.environ.get("FORTRESS_SPY_MAX_PROMPT_CHARS", "8000") or 8000)
    if len(prompt) > max_chars:
        prompt = prompt[:max_chars] + "\n...(trimmed)"
    text, usage = call_deepseek(prompt, max_out_tokens=640)
    decision = _parse_llm_json(text)
    action = (decision.get("action") or "wait").strip().lower()
    allowed = set(ALLOWED_ACTIONS)
    if is_eod_caution_window():
        allowed = filter_allowed_actions(allowed, eod_caution=True)
    if action not in allowed:
        action = "wait"
    decision["action"] = action
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"invalid action: {action}")
    decision["_raw_response"] = text[:4000]
    return decision, usage
