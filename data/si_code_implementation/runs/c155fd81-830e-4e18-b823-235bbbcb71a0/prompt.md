Implement this SI fix autonomously in the Fortress stack.

## Item
- ID: c155fd81-830e-4e18-b823-235bbbcb71a0
- Code: market_relative_underperformance
- Title: market_relative_underperformance
- Component: portfolio_session
- Impact: high

## Plan
1. Edit `fortress-ai/portfolio_session/entry_manager.py`:
   - In the `evaluate_entry_blocks` method (or equivalent), increment counters for each block type (denylist, pause_entries, pattern_disables) when an entry is rejected.
   - Store counters in a dict `self._block_counts`.
2. Edit `fortress-ai/portfolio_session/session_summary.py`:
   - In the `generate_summary` function, after processing all signals, read `entry_manager._block_counts`.
   - Add a new field `entry_block_breakdown` to the summary dict with counts per block type.
3. Edit `fortress-ai/portfolio_session/reporting.py`:
   - In the `format_session_report` function, include the `entry_block_breakdown` in the output log (info level) when the session has zero exits and positive benchmark movement.
   - Example log: "Entry blocks active: denylist=3, pause_entries=0, pattern_disables=5".

## Repos (absolute paths)
- fortress-ai: /home/ubuntu/fortress-ai
- trading-bot (Classic): /home/ubuntu/trading-bot

## Hard constraints
- Do NOT edit .env, .cursor/, data/, or weaken pre-trade gate / immutable caps
- NEVER edit protected files: utils/pre_trade_gate.py, utils/operator_halt.py, agents/risk_guardian.py, config/si_capability_registry.json, utils/si_code_implementation.py, SINGULARITY_HARDENING_PROMPT.md
- Only edit: agents/, utils/, config/, scripts/, tests/, dashboard/, deploy/ (non-protected)
- Minimize diff scope; match existing code style
- Add detectable log/block_reason markers: as appropriate
- Update config/si_fix_registry.json for code market_relative_underperformance if new mitigation
- Do NOT git commit — the SI runner commits after e2e

## Finish
Implement the fix completely, then summarize files changed and markers added.
