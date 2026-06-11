# Project State — Hardened Autonomous Singularity (sync as of 2026-06-11)

This file is the single source of truth for where the two-repo trading stack stands. Read it alongside `SINGULARITY_HARDENING_PROMPT.md` and `BRANCH_MODEL.md` before doing any further work.

## Repos & branches (CANONICAL)
| Repo | Commit to branch | Current HEAD | GitHub default |
|------|------------------|--------------|----------------|
| `fortress-ai` | `main` | `94b517a` | `main` ✅ |
| `trading-bot` | `master` | `cd2c24d` | `master` ✅ (reconciled 2026-06-11) |

- trading-bot's old `main` (`01e57ad`) is STALE and no longer default. Never commit to it.
- Verify `git rev-parse --abbrev-ref HEAD` before any commit.

## What has shipped (all verified clean, all merged to canonical branches)

### Phase 1 — Harden (fortress-ai, `8f05950`)
- `PROTECTED_PATHS` deny-list in `utils/si_code_implementation.py` `_diff_allowed()` (takes precedence over allow-list).
- SHA-256 integrity guard: snapshot → re-hash → auto-revert + abort on any protected-file change (`SI-FROZEN: protected_file_modified`).
- `is_trading_halted()` fails CLOSED; SI self-modification frozen during halt (`SI-FROZEN: trading_halted`) in both cycle entrypoints.
- Velocity cap counts attempts; single-flight `fcntl` lock at `data/si_code_implementation/.run.lock`.
- Tests: `tests/test_si_protected_paths.py`.

### Phase 2 — Unify (trading-bot, `cd2c24d`)
- Classic gate parity: BUY position-% cap (only tightens), symbol-format validation, `estimated_notional` passed on SELL + option paths.
- Broker-side OCO brackets on Classic stock entries (`utils/alpaca_execution.py::submit_entry_with_bracket`).
- `risk_guardian.py`: `threading.RLock` + `_sync_state_from_disk()` so the circuit breaker is authoritative across threads/processes.
- Tests: `tests/test_singularity_phase2.py`, extensions to `test_guardrail_extensions.py`.

### Phase 3 — Escalate (fortress-ai, merged at `0f4fc95`)
- `classic_bridge.py` bridge fix + e2e test proving Fortress learning reaches Classic's `si_recommendation_queue.json`.
- Expectancy-first objectives confirmed — NO win-rate primaries anywhere.
- Drawdown guard: bounded `max_rolling_drawdown_pct` knob (0.05–0.25, default 0.12) in `si_capability_registry.json`; `maybe_lift_aspire_targets` emits `SI-HOLD: drawdown_guard` and does not lift targets when breached.
- `PerformanceMonitor` auto-revert on rolling-expectancy regression / drawdown breach (NOT win-rate).
- Tests: `test_singularity_classic_bridge_e2e.py`, `test_si_objectives_expectancy_first.py`, `test_performance_monitor.py`, `test_si_singularity.py` (253 tests OK).

## Open items (tracked as GitHub issues on fortress-ai)
- **#4 (Low)** — Bracket path falls back to a naked market order on broker error. Fix: retry-or-skip, never submit unbracketed. (trading-bot `master`)
- **#5 (Low)** — Phase 3.4 auto-revert thresholds are hardcoded. Decide: keep hardcoded (safer, recommended) or move to bounded registry knobs. (fortress-ai `main`)

(Closed: #1 gate self-edit → Phase 1.1; #2 halt freeze → Phase 1.2; #3 Classic brackets → Phase 2.2.)

## Standing guardrails (unchanged, always in force)
- Never weaken `pre_trade_gate`, immutable risk caps, kill switch, or `operator_halt`. STOP + `# SI-BLOCKED:` rather than touch a protected file.
- Stays on paper. Do not flip `FORTRESS_LIVE_TRADING_ACK` / any live flag.
- Keep `FORTRESS_SI_AUTO_PUSH=0` until the operator explicitly enables autonomous push (rails are in place; operator decision pending).
- A weekday 7:00am ET drift monitor checks the protected files on GitHub and emails a green/yellow/red status. Baseline: `PHASE1_PROTECTED_BASELINE.json`.

## Protected-file hashes (current baseline — do not let these drift)
- fortress-ai (main): `pre_trade_gate.py`=dea2b8a, `operator_halt.py`=0ffd622, `si_code_implementation.py`=b276348
- trading-bot (master): `pre_trade_gate.py`=852120f, `operator_halt.py`=f02adee, `risk_guardian.py`=ef4834e
