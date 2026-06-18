Implement this SI fix autonomously in the Fortress stack.

## Item
- ID: c7e5a860-15b2-4f72-a970-9b91b6ccafc1
- Code: duplicate_entry_accumulation
- Title: Unified agent stacks entries on same symbol
- Component: unified_ai
- Impact: critical

## Plan
1. fortress-ai/src/agents/unified_agent.py: In enter_position method, add a pre-check that queries current positions for the same symbol. If symbol already held, log warning and return without executing. 
2. fortress-ai/src/risk/position_manager.py: Add method `flatten_oversized_positions(symbol, max_notional)` that iterates open positions, compares notional to FORTRESS_MAX_ORDER_NOTIONAL_USD, and generates chunked exit orders (e.g., split into 3-5 child orders). 
3. trading-bot/src/execution/order_sizer.py: Modify exit order creation to enforce FORTRESS_MAX_ORDER_NOTIONAL_USD by splitting large orders into chunks with random delays. 
4. fortress-ai/src/risk/pre_trade_gate.py: Add a deduplication rule that rejects enter_position if symbol already has an open position (configurable via env var). 
5. fortress-ai/src/config/constants.py: Ensure FORTRESS_MAX_ORDER_NOTIONAL_USD is defined and used consistently. 
6. Add unit tests for deduplication logic and chunking behavior.

## Repos (absolute paths)
- fortress-ai: /home/ubuntu/fortress-ai
- trading-bot (Classic): /home/ubuntu/trading-bot

## Hard constraints
- Do NOT edit .env, .cursor/, data/, or weaken pre-trade gate / immutable caps
- NEVER edit protected files: utils/pre_trade_gate.py, utils/operator_halt.py, agents/risk_guardian.py, config/si_capability_registry.json, utils/si_code_implementation.py, SINGULARITY_HARDENING_PROMPT.md
- Only edit: agents/, utils/, config/, scripts/, tests/, dashboard/, deploy/ (non-protected)
- Minimize diff scope; match existing code style
- Add detectable log/block_reason markers: already_holding, chunked_exit, enter_cooldown, entry_blocked_by_cooldown
- Update config/si_fix_registry.json for code duplicate_entry_accumulation if new mitigation
- Do NOT git commit — the SI runner commits after e2e

## Finish
Implement the fix completely, then summarize files changed and markers added.
