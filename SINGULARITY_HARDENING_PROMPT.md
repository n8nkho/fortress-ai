# Cursor Prompt — Fortress + Classic: Hardened Autonomous Singularity

> **For the Cursor agent.** Read this file in full before touching any code. It references real files in this repo (`fortress-ai`) and the sibling Classic repo (`trading-bot`). Implement the phases **in order**. Phase 1 is a hard prerequisite for Phases 2–3.

---

## Mission

Make the combined **Classic** (`trading-bot`) + **Fortress** (`fortress-ai`) stack self-improve **autonomously** toward **higher risk-adjusted expectancy with bounded drawdown** — *not* a literal "win rate above loss rate."

Why this framing: a system that optimizes for "% of trades that win" is trivially gamed (tiny take-profits + huge stops → 95% win rate that blows up on the 5%). The durable, mathematically honest objective is **positive expectancy per unit of risk, with a hard cap on drawdown**. The existing `config/si_singularity.json` already encodes this correctly (`combined_rolling_realized_usd`, `skim_payoff_ratio`) — preserve that intent everywhere.

## Absolute constraints (never violate)

1. **Never weaken `pre_trade_gate`, immutable risk caps, the kill switch, or the operator halt.** Not the values, not the logic, not by widening allow-lists. If a change *requires* touching these, **STOP** and leave a `# SI-BLOCKED: <reason>` comment instead.
2. **Never edit** `.env`, `data/`, `.cursor/`, `venv/`, or any secret/credential. Use `.env.example` only to understand config shape.
3. **The system stays on paper trading.** Do not flip any live-trading flag or ack string.
4. When a protected path would be modified, STOP and emit the `# SI-BLOCKED:` marker — do not work around it.

---

## Phase 1 — HARDEN (prerequisite; do this first)

The autonomous code-writer can currently edit its own safety rails. Close that before escalating anything.

### 1.1 Deny-list + integrity guard for the self-coder
- File: `utils/si_code_implementation.py`.
- Add an explicit `PROTECTED_PATHS` **deny-list** that takes precedence over `ALLOWED_WRITE_PREFIXES`. It must include (both repos):
  - `agents/pre_trade_gate.py`, `utils/pre_trade_gate.py` (whichever exist in each repo)
  - the risk-cap constants module(s) (`risk_guardian.py` and anything defining `FORTRESS_MAX_ORDER_*`)
  - the kill switch / `operator_halt.py`
  - `config/si_capability_registry.json` (min/max bounds must not be self-edited)
  - this file itself (`utils/si_code_implementation.py`) and the diff-allow logic
- In `_diff_allowed()`: reject any diff whose target matches `PROTECTED_PATHS`, regardless of prefix allow-list.
- Add a **SHA-256 integrity guard**: snapshot hashes of all protected files at cycle start; after any agent run, re-hash and **auto-revert + abort** if any protected file changed. Emit `SI-FROZEN: protected_file_modified <path>`.
- Add `tests/test_si_protected_paths.py` covering: deny-list blocks a protected-path diff, integrity guard reverts an out-of-band change.

### 1.2 Halt must freeze self-modification
- `is_trading_halted()` today only blocks order submission. The SI loops keep tuning/coding/pushing during a halt.
- In **both** cycle entrypoints — `utils/rth_autonomous_si.py::run_rth_intraday_cycle` and the `run_autonomous_code_si_cycle` path — check halt **before** any self-tuning/coding/push step and short-circuit with `SI-FROZEN: trading_halted`.
- Make `is_trading_halted()` **fail closed** (treat read error as halted), reversing the current fail-open behavior.
- Add a test asserting a halt freezes both tuning and coding cycles.

### 1.3 Velocity cap + single-flight lock
- `_implementations_today()` counts only `ok=True` runs, so failed attempts still launch agents and the per-day cap is bypassable.
- Count **attempts**, not just successes.
- Add a single-flight **file lock** at `data/si_code_implementation/.run.lock` so concurrent cycles can't race the counter.
- Add a test for the velocity cap counting attempts and the lock preventing concurrent entry.

**Phase 1 acceptance:** `e2e_verify.sh --no-ingest` exits 0; all new pytest pass; a dry run produces **zero** diffs against any `PROTECTED_PATHS` entry.

---

## Phase 2 — UNIFY & VERIFY

### 2.1 Bring Classic's gate up to Fortress strength (do not weaken values)
- Classic `trading-bot` gate is thinner than Fortress's. Without lowering any threshold:
  - add symbol-format validation,
  - enforce a BUY position-percentage cap,
  - pass `estimated_notional` on **every** order path (Classic SELL and option paths currently pass `None`).

### 2.2 Broker-side brackets on Classic stock entries
- Classic `execute_buy_order`/`execute_sell_order` (`orchestrator.py`) send bare `MarketOrderRequest` DAY orders and rely on `exit_monitor` polling. Mirror Fortress's `submit_entry_with_bracket` (`utils/alpaca_execution.py`) so stops/targets are **broker-side OCO**, surviving a process crash.

### 2.3 risk_guardian state safety
- The consecutive-loss counter lives in module globals across `asyncio.to_thread` with no lock and is process-local. Add a lock and **re-read the state file** so the circuit breaker (reduce@3 / halt@5) is authoritative across threads/processes.

**Phase 2 acceptance:** both repos' e2e suites and smoke tests pass.

---

## Phase 3 — ESCALATE (bounded autonomy)

### 3.1 Verify the bridge end-to-end
- Confirm/test the `si_singularity` → `classic_bridge` path so Fortress's learning actually reaches Classic, with an e2e test.

### 3.2 Expectancy-first objective hygiene
- Audit all SI objective configs. **No win-rate as a primary target.** Keep `combined_rolling_realized_usd` / `skim_payoff_ratio` as the optimization targets.

### 3.3 Drawdown guard on target lifting
- In `maybe_lift_aspire_targets`, add a new **bounded** `max_rolling_drawdown_pct` knob (registered in `config/si_capability_registry.json` with min/max). If rolling drawdown breaches it, **do not lift aspire targets** — emit `SI-HOLD: drawdown_guard`.

### 3.4 Auto-revert on regression
- Confirm/implement `PerformanceMonitor` auto-revert: if a self-applied change degrades rolling expectancy over a window, revert it automatically.

**Phase 3 acceptance:** bridge e2e passes; no win-rate primary targets remain; drawdown guard and auto-revert covered by tests.

---

## Global finish (every phase)

- **Minimize the diff.** Touch only what each step requires.
- Use consistent log markers: `SI-BLOCKED:` (refused to touch protected path), `SI-FROZEN:` (halt/integrity stop), `SI-HOLD:` (bounded-autonomy guard tripped).
- **Do NOT `git commit` or `git push`.** The runner handles that. Just leave the working tree changed.
- End with a summary of files changed and an explicit confirmation: **zero protected-file modifications**.

---

## Notes (for the human operator, not Cursor)

- Keep `FORTRESS_SI_AUTO_PUSH=0` until Phase 1 lands and its tests are green. Phase 1 is what makes auto-push safe.
- Honest take: "probability of win always higher than losses" is not achievable as a literal guarantee — optimizing raw win-rate invites overfitting and martingale-style blowups. The durable version of that goal is **positive risk-adjusted expectancy with a hard drawdown cap**, which is what this prompt builds toward.
