# Repo & Branch Model — READ BEFORE COMMITTING (Cursor)

This is the canonical branch map for the two-repo trading stack. **Always commit to the branches listed here.** A prior Phase 1 commit landed on the right branch in `trading-bot` (`master`) but the repo's GitHub *default* is `main`, which is stale — this file exists so that mistake is never repeated when implementing Phase 2–3.

## Canonical branches

| Repo | Canonical branch | GitHub default | Status |
|------|------------------|----------------|--------|
| `fortress-ai` | `main` | `main` | ✅ aligned — commit here |
| `trading-bot` | **`master`** | `main` ⚠️ | ⚠️ default is WRONG — see below |

### fortress-ai
- Canonical = `main`. Default = `main`. No ambiguity. Commit Phase 2–3 fortress work to `main`.
- Phase 1 landed at `8f05950`.

### trading-bot — IMPORTANT
- **Canonical / deployed branch = `master`.** Commit ALL trading-bot work to `master`.
- The GitHub **default branch is `main`, and it is STALE**: `master` is **161 commits ahead of `main`, 0 behind**.
- `main`'s `utils/operator_halt.py` still **fails OPEN** on a halt-state read error (`return False` in the `except`). `master`'s correctly **fails CLOSED** (`return True`). The Phase 1.2 fix lives only on `master`.
- Phase 1 landed at `20d079b` on `master`.
- **Do NOT branch from, merge into, or commit to `main`.** Do NOT merge `main` into `master` (that would drag stale code over the good branch). If anything needs to reach `main`, the direction is `master` → `main`, and only a human does that.

## Pending reconciliation (human-owned, do not perform autonomously)
The operator will resolve the `main`/`master` split by one of: switching the GitHub default to `master`, merging `master` → `main`, or deleting `main`. Until that happens, treat `master` as the only source of truth for `trading-bot`.

## Rules for Cursor when implementing Phase 2–3
1. **trading-bot → branch `master`. fortress-ai → branch `main`.** Verify with `git rev-parse --abbrev-ref HEAD` before any commit in each repo.
2. Never weaken `pre_trade_gate`, the immutable risk caps, the kill switch, or `operator_halt` (still bound by `SINGULARITY_HARDENING_PROMPT.md`).
3. Do not change either repo's default branch or reconcile `main`/`master` yourself — that is operator-owned.
4. If you find yourself about to commit trading-bot work to `main`, STOP and leave a `# SI-BLOCKED: wrong_branch_main` note instead.
