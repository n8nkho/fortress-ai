# Future work (tracked, not scheduled)

Items here are deliberate deferrals — not bugs.

## Prompt-variant walk-forward (per-candidate evaluation)

**GitHub:** https://github.com/n8nkho/fortress-ai/issues/6

**Status:** Future scope — do not build under time pressure.

**Context:** `FORTRESS_PROMPT_WF_GATE_ENABLED` / `FORTRESS_PROMPT_LEDGER_HEALTH_GATE_ENABLED` implements a
**ledger health gate** only: it blocks Tier-2 prompt promotion when realized PnL shows late-window
degradation vs early window. It does **not** evaluate whether a specific prompt candidate would have
performed better on historical decisions.

**Desired end state:** Replay historical decision contexts under candidate prompt text (or appendix),
score outcomes vs baseline, and gate promotion on candidate-specific stability — analogous to rule-param
walk-forward but on the prompt evaluation surface.

**Rough scope:**

- Tag decisions with `prompt_variant` / overlay version at decision time (partially present).
- Offline replay job: re-run critique/decision logic with candidate appendix on frozen feature rows.
- Separate report artifact per candidate (`prompt_variant_wf_report_<id>.json`).
- New env gate distinct from ledger health (`FORTRESS_PROMPT_VARIANT_WF_ENABLED` or similar).

**References:** `utils/prompt_walk_forward_gate.py`, `agents/walk_forward_validator.py`, `agents/prompt_evolution.py`.
