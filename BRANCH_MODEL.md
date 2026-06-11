# Repo & Branch Model — READ BEFORE COMMITTING (Cursor)

This is the canonical branch map for the two-repo trading stack. **Always commit to the branches listed here.** A prior Phase 1 commit landed on the right branch in `trading-bot` (`master`) but the repo's GitHub *default* is `main`, which is stale — this file exists so that mistake is never repeated when implementing Phase 2–3.

## Canonical branches

| Repo | Canonical branch | GitHub default | Status |
|------|------------------|----------------|--------|
| `fortress-ai` | `main` | `main` | ✅ aligned — commit here |
| `trading-bot` | **`master`** | **`master`** ✅ | ✅ reconciled 2026-06-11 — master is now the default |

### fortress-ai
- Canonical = `main`. Default = `main`. No ambiguity. Commit Phase 2–3 fortress work to `main`.
- Phase 1 landed at `8f05950`.

### trading-bot — RECONCILED
- **Canonical / deployed branch = `master`, and as of 2026-06-11 it is the GitHub DEFAULT branch.** Commit ALL trading-bot work to `master`.
- The old `main` (`01e57ad`) is retained but **stale and no longer default**. Its `operator_halt.py` fails OPEN — do NOT use it.
- `master`'s `operator_halt.py` correctly **fails CLOSED** (`return True` on read error).
- Phase 1 landed at `20d079b`; Phase 2 at `cd2c24d` (current `master` HEAD).
- **Do NOT branch from, merge into, or commit to `main`.** Never merge `main` into `master`.

## Reconciliation status: DONE
The `main`/`master` split was reconciled on 2026-06-11 by setting `master` as the GitHub default branch. No further action needed unless `master` ceases to be default.

## Rules for Cursor when implementing Phase 2–3
1. **trading-bot → branch `master`. fortress-ai → branch `main`.** Verify with `git rev-parse --abbrev-ref HEAD` before any commit in each repo.
2. Never weaken `pre_trade_gate`, the immutable risk caps, the kill switch, or `operator_halt` (still bound by `SINGULARITY_HARDENING_PROMPT.md`).
3. Do not change either repo's default branch or reconcile `main`/`master` yourself — that is operator-owned.
4. If you find yourself about to commit trading-bot work to `main`, STOP and leave a `# SI-BLOCKED: wrong_branch_main` note instead.
