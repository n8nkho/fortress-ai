# Repo & Branch Model — READ BEFORE COMMITTING (Cursor)

This is the canonical branch map for the two-repo trading stack. **Always commit to the branches listed here.**

## Canonical branches

| Repo | Canonical branch | GitHub default | Status |
|------|------------------|----------------|--------|
| `fortress-ai` | `main` | `main` | ✅ aligned — commit here |
| `trading-bot` | **`master`** | **`master`** ✅ | ✅ reconciled — master is default |

### fortress-ai
- Canonical = `main`. Default = `main`. No ambiguity.
- Phase 1 at `8f05950`; Phase 3 + issue #5 at `1ff2380`; docs sync at `94fa853`.

### trading-bot
- **Canonical / deployed branch = `master`** (GitHub default). Commit ALL trading-bot work to `master`.
- Phase 2 at `cd2c24d`; issue #4 bracket hold merged at `8c9836d`.
- A legacy `main` branch may still exist on the remote (stale, fails-open halt) — **do not use it**. Operator may delete it during hygiene cleanup.

## Rules for Cursor
1. **trading-bot → branch `master`. fortress-ai → branch `main`.** Verify with `git rev-parse --abbrev-ref HEAD` before any commit.
2. Never weaken `pre_trade_gate`, the immutable risk caps, the kill switch, or `operator_halt` (still bound by `SINGULARITY_HARDENING_PROMPT.md`).
3. Do not change either repo's default branch yourself — that is operator-owned.
4. If you find yourself about to commit trading-bot work to `main`, STOP and leave a `# SI-BLOCKED: wrong_branch_main` note instead.
5. Feature/review branches (`fix-issue-*`, `hygiene-cleanup`, etc.) merge via PR — do not push directly to canonical branches unless the operator explicitly requests it.
