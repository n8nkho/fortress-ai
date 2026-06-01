# Self-improvement & governance (tiers 0–3)

## Risk tiers (parameter tuning)

| Tier | Meaning | Behavior |
|------|---------|----------|
| **0** | Low-risk bounded params (`confidence_threshold`, `decision_interval`) | Shadow test → **auto-approve** if `utils/improvement_governance.meets_auto_approve_criteria` passes (uses proxy metrics when win rate is unknown). |
| **1** | Medium (`rsi_entry_threshold`, `position_size_pct`) | Shadow test → **24h veto window** (`data/governance_veto_pending.json`); auto-apply after deadline via `POST /api/governance/process-veto-windows` or `scripts/improvement_cron_example.sh`. |
| **2** | Prompt / strategy | Human approval via **`/api/prompt_evolution/*`** and dashboard (additive appendix only). |
| **3** | Immutable | **Blocked** — names in `IMMUTABLE_PARAM_NAMES` in `utils/improvement_governance.py`. |

Artifacts: `data/improvement_proposals.jsonl`, `data/improvement_outcomes.jsonl`, `data/governance_decisions.jsonl`, `data/tunable_params.json` (snapshot), plus existing `self_improvement_log.jsonl`.

---

# Tier-1 self-improvement (parameter tuning)

Fortress AI can propose **single-parameter** adjustments within fixed bounds. Risk rails enforced by the pre-trade gate and environment (position size, exposure, stops, halt) are **immutable** here—the engine never writes those keys.

## Tunable parameters

| Parameter | Bounds | Applied via |
|-----------|--------|-------------|
| `confidence_threshold` | 0.6–0.95 | `data/tunable_params_overrides.json` → `get_confidence_threshold()` |
| `decision_interval` | 120–1800 s | Overrides → loop sleep in `unified_ai_agent` |
| `rsi_entry_threshold` | 35–50 (integer) | Prompt text + env default `FORTRESS_AI_RSI_ENTRY_THRESHOLD` |

## Approval flow

1. **Propose** — `POST /api/self_improvement/propose` runs analysis on recent `ai_decisions.jsonl`, optional DeepSeek proposal (`DEEPSEEK_API_KEY`), else heuristic fallback.
2. **Shadow** — A proxy comparison on recent logs (not full market replay). Full shadow metrics require closed-trade PnL history.
3. **Auto-approve** — Disabled until sufficient measured win-rate history exists; otherwise status is `pending_human`.
4. **Human** — Dashboard **Apply override** calls `POST /api/self_improvement/approve` with `proposal_id`. **Reject** clears pending. **Revert** deletes override file.
5. **Velocity** — At most **1** applied change per rolling 7 days and **3** per rolling 31 days (counts `approved_human` and `auto_approved` in the JSONL log).

## Safety

- **Monitor** — `POST /api/self_improvement/monitor` runs `monitor_and_revert_if_needed()`: if a measured win rate exists and drops below **0.75**, overrides are cleared and self-improvement is halted (see `data/self_improvement_state.json`).
- **Audit** — Every proposal, shadow snapshot, approval, revert, and rejection is appended to `data/self_improvement_log.jsonl`.
- **Pending** — Awaiting human review is stored in `data/self_improvement_pending.json`.

## Environment

- `FORTRESS_AI_DATA_DIR` — Optional root for all of the above paths (default: `./data`).
- `FORTRESS_AI_MIN_CONFIDENCE` — Baseline when no override is set; **`get_confidence_threshold()` never goes below this**.
- `FORTRESS_AI_CONFIDENCE_FLOOR_LOCK=1` — Blocks SI auto-tuning from **lowering** confidence (default on in `.env.example`).
- `FORTRESS_AI_ELIGIBLE_UNIVERSE` — Symbols unified AI may enter (off skim/infra denylist).
- `FORTRESS_AI_RSI_ENTRY_THRESHOLD` — Baseline RSI entry hint when no override is set.
- `DEEPSEEK_API_KEY` — Enables LLM proposals; omit for heuristic-only proposals.

---

# Recursive SI queue (integrity scan)

Daily / RTH pipeline: `utils/integrity_diagnostics.py` → `utils/si_recommendation_queue.py` → `data/si_recommendation_queue.json`.

| Disposition | Meaning |
|-------------|---------|
| `auto_applied` | Bounded tunable nudge (Tier 0/1) |
| `pending_agent_review` | Cursor agent triages → user go-ahead for code |
| `pending_human_go` | User must approve before implementation |

Fix registry: `config/si_fix_registry.json`. Session learnings: `config/session_learnings_YYYYMMDD.json`.

**Not auto-applied:** `low_unified_execution_rate` (monitor only when confidence floor lock is on). Prefer off-denylist watchlist over lowering confidence.

API: `GET /api/si/recommendations`, governance cron `scripts/governance_maintenance.py`.

See `.cursor/rules/recursive-si-workflow.mdc` for agent review workflow.

---

# Tier-2 prompt evolution (additive appendix)

Tier 2 **does not replace** the JSON schema instructions in `build_prompt`. It only appends a short `ADDITIONAL_OPERATOR_GUIDANCE` block from human-approved text (see `utils/prompt_evolution_store.validate_appendix_text` blocklist).

## Data files

| File | Purpose |
|------|---------|
| `data/prompt_evolution_overlay.json` | Active approved appendix text |
| `data/prompt_evolution_pending.json` | Proposal awaiting approval or A/B start |
| `data/prompt_evolution_config.json` | A/B window (`ab_test.active`, baseline vs candidate, `ends_utc`) |
| `data/prompt_evolution_log.jsonl` | Audit trail |

Each logged decision may include `prompt_variant` (`baseline`, `overlay`, `A_baseline`, `B_candidate`) for effectiveness analysis.

## API (dashboard mirrors these)

- `GET /api/prompt_evolution/status` — overlay preview, pending, A/B state, recent events
- `POST /api/prompt_evolution/analyze` — effectiveness snapshot from `ai_decisions.jsonl`
- `POST /api/prompt_evolution/propose` — create pending (DeepSeek or heuristic; subject to monthly velocity)
- `POST /api/prompt_evolution/approve` — promote pending → overlay (requires JSON body `proposal_id` when matching)
- `POST /api/prompt_evolution/reject` — discard pending
- `POST /api/prompt_evolution/revert` — delete overlay file
- `POST /api/prompt_evolution/ab/start` — body `{ "duration_days": 7 }`; locks pending candidate vs current baseline for alternating cycles
- `POST /api/prompt_evolution/ab/end` — body `{ "winner": "A" | "B" | "discard" }` to finalize test

## Velocity & safety

- At most **2** prompt-evolution approvals / starts per rolling **31** days (see `agents/prompt_evolution.py`).
- **No auto-approve** for prompt text.
- A/B alternates by `len(state["last_actions"]) % 2` while the test is active and before `ends_utc`.

Use Tier 2 only after Tier 1 has been stable in your environment; keep human review for every production overlay change.

---

## Position size % (live orders)

`get_position_size_pct()` (env + `tunable_params_overrides.json`) caps **BUY** notional to **equity × pct** in `evaluate_pre_trade_submission`, and `unified_ai_agent.act()` **clamps qty** before the gate when Alpaca equity + price are known.

## PnL-based metrics (shadow + monitors)

When decision rows include realized PnL (e.g. `pnl`, `act.detail.pnl`, `realized_pnl_usd`), **`utils/decision_log_metrics`** computes win rate and drawdown on **executed `enter_position`** rows. Shadow testing then produces numeric **`win_rate_delta`** / **`max_drawdown_delta`** for confidence-threshold proposals instead of relying only on proxies.
